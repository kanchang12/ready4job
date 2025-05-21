from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import os
import json
import secrets
from flask_socketio import SocketIO, emit

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_for_testing')

# Configure PostgreSQL database
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'postgresql://postgres:password@localhost:5432/ready4job'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Define database models
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    credits = db.Column(db.Integer, default=5)  # New users start with 5 credits
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reports = db.relationship('Report', backref='user', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def deduct_credit(self):
        if self.credits > 0:
            self.credits -= 1
            db.session.commit()
            return True
        return False

class Report(db.Model):
    __tablename__ = 'reports'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.JSON, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    payment_id = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if email already exists
        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email already registered')
            return redirect(url_for('register'))
        
        # Create new user
        new_user = User(email=email)
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if not user or not user.check_password(password):
            flash('Invalid email or password')
            return redirect(url_for('login'))
        
        login_user(user)
        return redirect(url_for('dashboard'))
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.timestamp.desc()).all()
    return render_template('dashboard.html', reports=reports)

@app.route('/interview')
@login_required
def interview():
    if current_user.credits <= 0:
        flash('You do not have enough credits for an interview')
        return redirect(url_for('dashboard'))
    
    return render_template('interview.html')

@app.route('/api/reports')
@login_required
def get_reports():
    reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.timestamp.desc()).all()
    result = []
    
    for report in reports:
        result.append({
            'id': report.id,
            'content': report.content,
            'timestamp': report.timestamp.isoformat()
        })
    
    return jsonify(result)

@app.route('/api/report/<int:report_id>')
@login_required
def get_report(report_id):
    report = Report.query.filter_by(id=report_id, user_id=current_user.id).first()
    
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    return jsonify({
        'id': report.id,
        'content': report.content,
        'timestamp': report.timestamp.isoformat()
    })

@app.route('/report/<int:report_id>')
@login_required
def view_report(report_id):
    report = Report.query.filter_by(id=report_id, user_id=current_user.id).first()
    
    if not report:
        flash('Report not found')
        return redirect(url_for('dashboard'))
    
    return render_template('report.html', report=report)

@app.route('/purchase', methods=['GET', 'POST'])
@login_required
def purchase():
    if request.method == 'POST':
        credits_amount = int(request.form.get('credits_amount', 0))
        
        # Simple payment simulation
        if credits_amount > 0:
            # In a real app, you would integrate with a payment gateway here
            payment = Payment(
                user_id=current_user.id,
                amount=credits_amount * 2.0,  # $2 per credit
                credits=credits_amount,
                payment_id=f"sim_{secrets.token_hex(8)}"  # Simulated payment ID
            )
            
            current_user.credits += credits_amount
            
            db.session.add(payment)
            db.session.commit()
            
            flash(f'Successfully purchased {credits_amount} credits')
            return redirect(url_for('dashboard'))
    
    return render_template('purchase.html')

# Socket.IO event handlers
@socketio.on('start_interview')
def handle_start_interview():
    if current_user.credits <= 0:
        emit('error', {'message': 'Not enough credits'})
        return

    # Setup interview session
    session['interview_id'] = f"interview_{secrets.token_hex(8)}"
    
    # Send initial message
    emit('interview_started', {'message': 'Interview started'})
    
    # Send first question (the real interview is handled by ElevenLabs widget)
    emit('interviewer_message', {
        'text': "Hello! I'm your AI interviewer. Let's start with you telling me a bit about yourself and your background."
    })

@socketio.on('user_message')
def handle_user_message(data):
    if not current_user.is_authenticated:
        emit('error', {'message': 'Authentication required'})
        return

    # Get the message text
    user_text = data.get('text', '')
    
    # Log the message
    app.logger.info(f"User message: {user_text}")
    
    # In this implementation, we don't need to do anything with the message
    # since the real conversation is handled by the hidden ElevenLabs widget
    # But we acknowledge receipt and keep the UI flow going
    
    # Simulate processing time
    socketio.sleep(1)
    
    # End the interview after some time (for demo purposes)
    # In a real implementation, this would be triggered by the ElevenLabs widget
    # when the interview is complete
    if "end interview" in user_text.lower():
        # End the interview
        complete_interview()
    else:
        # Otherwise, just send a generic follow-up question
        emit('interviewer_message', {
            'text': "Thank you for sharing that. Could you tell me about a challenging situation you faced at work and how you handled it?"
        })

@socketio.on('user_speaking')
def handle_user_speaking(data):
    # This handler just acknowledges when the user is speaking
    # It's used to coordinate the UI state with the hidden ElevenLabs widget
    is_active = data.get('active', False)
    app.logger.info(f"User speaking: {is_active}")

def complete_interview():
    """
    Function to end the interview and generate a report
    """
    # Create a simple report (in a real implementation, this would use data from ElevenLabs)
    content = {
        'conversation_id': session.get('interview_id', 'unknown'),
        'overall': 'Good',
        'strengths': 'Clear communication, provided relevant examples',
        'improvement': 'Be more concise with answers, focus more on specific achievements',
        'detailed_feedback': {
            'communication': 'Effective communication skills demonstrated',
            'content': 'Answers were relevant and informative',
            'delivery': 'Good pace and articulation'
        }
    }
    
    # Create a new report
    try:
        report = Report(user_id=current_user.id, content=content)
        
        # Deduct credit
        current_user.deduct_credit()
        
        # Save to database
        db.session.add(report)
        db.session.commit()
        
        # Notify client that interview is complete
        socketio.emit('interview_completed', {'success': True, 'report_id': report.id}, room=request.sid)
        
    except Exception as e:
        app.logger.error(f"Error creating report: {str(e)}")
        socketio.emit('error', {'message': 'Error creating report'}, room=request.sid)

# Create database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=True, host='0.0.0.0', port=port)
