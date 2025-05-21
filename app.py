from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import os
import json
import websockets
import asyncio
from flask_socketio import SocketIO, emit
import secrets
import ssl
import certifi
import requests

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

# Configure Eleven Labs API keys
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY', '')
ELEVENLABS_AGENT_ID = os.environ.get('ELEVENLABS_AGENT_ID', '')
ELEVENLABS_BASE_URL = os.environ.get('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io')

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

# WebSocket handling for interview
@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False
    emit('status', {'message': 'Connected to interview server'})

@socketio.on('disconnect')
def handle_disconnect():
    emit('status', {'message': 'Disconnected from interview server'})

@socketio.on('start_interview')
def handle_start_interview():
    if current_user.credits <= 0:
        emit('error', {'message': 'Not enough credits'})
        return
    
    # Start the interview process
    emit('interview_started', {'message': 'Interview started'})
    
    # Connect to Eleven Labs and start the interview
    try:
        # Use Eleven Labs REST API to initialize the conversation
        headers = {
            'xi-api-key': ELEVENLABS_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Initialize the conversation with Eleven Labs
        init_data = {
            'user_id': str(current_user.id),
            'agent_id': ELEVENLABS_AGENT_ID,
            'callback_url': f"{request.host_url.rstrip('/')}/api/interview/callback",
            'metadata': {
                'session_type': 'interview',
                'user_email': current_user.email
            }
        }
        
        # Make the initialization request to Eleven Labs
        response = requests.post(
            f"{ELEVENLABS_BASE_URL}/v1/convai/conversations", 
            headers=headers,
            json=init_data
        )
        
        if response.status_code == 200:
            conversation_data = response.json()
            session['conversation_id'] = conversation_data.get('conversation_id')
            
            # Send initial message from the interviewer
            emit('interviewer_message', {
                'text': 'Hello! I\'m your AI interviewer. Let\'s start with you telling me a bit about yourself and your background.'
            })
        else:
            app.logger.error(f"Error initializing Eleven Labs conversation: {response.text}")
            emit('error', {'message': 'Error connecting to interview service. Please try again.'})
            
    except Exception as e:
        app.logger.error(f"Error starting interview: {str(e)}")
        emit('error', {'message': f'Error starting interview: {str(e)}'})

@socketio.on('user_message')
def handle_user_message(data):
    if not current_user.is_authenticated:
        emit('error', {'message': 'Authentication required'})
        return
    
    user_text = data.get('text', '')
    conversation_id = session.get('conversation_id')
    
    if not conversation_id:
        emit('error', {'message': 'Conversation not initialized. Please restart the interview.'})
        return
    
    # Connect to Eleven Labs API
    try:
        headers = {
            'xi-api-key': ELEVENLABS_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Send user message to Eleven Labs
        message_data = {
            'text': user_text,
            'conversation_id': conversation_id,
            'user_id': str(current_user.id)
        }
        
        # Make the request to Eleven Labs
        response = requests.post(
            f"{ELEVENLABS_BASE_URL}/v1/convai/messages", 
            headers=headers,
            json=message_data
        )
        
        if response.status_code == 200:
            message_response = response.json()
            ai_response = message_response.get('text', 'I did not understand that. Could you please rephrase?')
            
            # Check if interview is complete
            if message_response.get('metadata', {}).get('interview_complete', False):
                emit('interviewer_message', {'text': ai_response})
                emit('interview_completed', {'message': 'Interview completed'})
            else:
                emit('interviewer_message', {'text': ai_response})
                
        else:
            app.logger.error(f"Error sending message to Eleven Labs: {response.text}")
            emit('error', {'message': 'Error communicating with interview service. Please try again.'})
            
    except Exception as e:
        app.logger.error(f"Error sending message: {str(e)}")
        emit('error', {'message': f'Error communicating with interview service: {str(e)}'})

# Endpoint to receive transcript and summary from Eleven Labs
@app.route('/api/interview/callback', methods=['POST'])
def interview_callback():
    """
    Receive callback from Eleven Labs with transcript and summary
    This endpoint should be configured in Eleven Labs as the callback URL
    """
    # Verify request is from Eleven Labs (you might implement signature verification)
    # In production, implement proper validation
    
    if not request.is_json:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    data = request.get_json()
    
    # Extract data from callback
    user_id = data.get('user_id')
    transcript = data.get('transcript', {})
    summary = data.get('summary', {})
    
    # Find the user
    user = User.query.filter_by(id=user_id).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Create report from transcript and summary
    content = {
        'transcript': transcript,
        'overall': summary.get('overall_assessment', 'Good'),
        'strengths': summary.get('strengths', 'Clear communication'),
        'improvement': summary.get('areas_for_improvement', 'Be more concise with answers'),
        'detailed_feedback': summary.get('detailed_feedback', {})
    }
    
    # Create a new report
    report = Report(user_id=user.id, content=content)
    
    # Deduct credit
    user.deduct_credit()
    
    # Save to database
    db.session.add(report)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'report_id': report.id
    }), 201

# Create database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, debug=True, host='0.0.0.0', port=port) per credit
                credits=credits_amount,
                payment_id=f"sim_{secrets.token_hex(8)}"  # Simulated payment ID
            )
            
            current_user.credits += credits_amount
            
            db.session.add(payment)
            db.session.commit()
            
            flash(f'Successfully purchased {credits_amount} credits')
            return redirect(url_for('dashboard'))
    
    return render_template('purchase.html')

# WebSocket handling for interview
@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False
    emit('status', {'message': 'Connected to interview server'})

@socketio.on('disconnect')
def handle_disconnect():
    emit('status', {'message': 'Disconnected from interview server'})

@socketio.on('start_interview')
def handle_start_interview():
    if current_user.credits <= 0:
        emit('error', {'message': 'Not enough credits'})
        return
    
    # Start the interview process
    emit('interview_started', {'message': 'Interview started'})
    
    # Simulate connection to Eleven Labs
    emit('interviewer_message', {'text': 'Hello! I\'m your AI interviewer. Tell me about yourself and your background.'})

@socketio.on('user_message')
async def handle_user_message(data):
    if not current_user.is_authenticated:
        emit('error', {'message': 'Authentication required'})
        return
    
    user_text = data.get('text', '')
    
    # Connect to Eleven Labs WebSocket
    try:
        # Create WebSocket connection to Eleven Labs
        # We're doing this per message to handle session state properly
        # In a production app, you might maintain connection in user session
        eleven_labs_uri = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}&user_id={current_user.id}"
        
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        
        # Use websockets library to connect to Eleven Labs
        async with websockets.connect(eleven_labs_uri, extra_headers=headers) as eleven_ws:
            # Send user message to Eleven Labs
            await eleven_ws.send(json.dumps({
                "text": user_text,
                "user_id": str(current_user.id)
            }))
            
            # Wait for response
            response = await eleven_ws.recv()
            response_data = json.loads(response)
            
            # Extract text response from Eleven Labs
            ai_response = response_data.get('text', 'Sorry, I did not understand that')
            
            # Check if interview is completed
            if "INTERVIEW_COMPLETED" in response_data.get('metadata', {}).get('flags', []):
                emit('interviewer_message', {'text': ai_response})
                emit('interview_completed', {'message': 'Interview completed'})
                return
            
            # Send AI response to client
            emit('interviewer_message', {'text': ai_response})
    
    except Exception as e:
        app.logger.error(f"Error connecting to Eleven Labs: {str(e)}")
        emit('error', {'message': f'Error communicating with interviewer service: {str(e)}'})

# Endpoint to receive transcript and summary from Eleven Labs
@app.route('/api/interview/callback', methods=['POST'])
def interview_callback():
    """
    Receive callback from Eleven Labs with transcript and summary
    This endpoint should be configured in Eleven Labs as the callback URL
    """
    # Verify request is from Eleven Labs (you might implement signature verification)
    # In production, implement proper validation
    
    if not request.is_json:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    data = request.get_json()
    
    # Extract data from callback
    user_id = data.get('user_id')
    transcript = data.get('transcript', {})
    summary = data.get('summary', {})
    
    # Find the user
    user = User.query.filter_by(id=user_id).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Create report from transcript and summary
    content = {
        'transcript': transcript,
        'overall': summary.get('overall_assessment', 'Good'),
        'strengths': summary.get('strengths', 'Clear communication'),
        'improvement': summary.get('areas_for_improvement', 'Be more concise with answers'),
        'detailed_feedback': summary.get('detailed_feedback', {})
    }
    
    # Create a new report
    report = Report(user_id=user.id, content=content)
    
    # Deduct credit
    user.deduct_credit()
    
    # Save to database
    db.session.add(report)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'report_id': report.id
    }), 201

# Create database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))