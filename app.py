from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import os
import json
import secrets
from flask_socketio import SocketIO, emit
import io # Added for audio handling
import re # Added for robust category parsing

# --- NEW: AI API Imports ---
import google.generativeai as genai
from google.cloud import texttospeech

# Initialize Flask app
app = Flask(__name__)
# IMPORTANT: For production, set a strong, unpredictable SECRET_KEY as an environment variable
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_for_testing_replace_in_prod')

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
# Use MessageQueue for SocketIO if deploying with multiple workers (e.g. Redis)
# For single process local dev, default is fine.
# If you are using a non-gevent/eventlet server (e.g. Flask's default development server),
# `async_mode` can be left as default (threading). If using gunicorn/eventlet, configure here.
socketio = SocketIO(app, cors_allowed_origins="*")

# --- NEW: Configure Google AI (Gemini) ---
# Ensure GOOGLE_API_KEY and GOOGLE_APPLICATION_CREDENTIALS are set as environment variables
# For local testing, you can temporarily hardcode GOOGLE_API_KEY here, but REMOVE FOR PRODUCTION!
# os.environ["GOOGLE_API_KEY"] = "YOUR_GEMINI_API_KEY_HERE"
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
gemini_model = genai.GenerativeModel('gemini-1.5-flash') # Using the cost-effective Flash model

# --- NEW: Configure Google Cloud Text-to-Speech ---
# GOOGLE_APPLICATION_CREDENTIALS env var should point to your service account key JSON file
# For local testing: export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your-gcp-key.json"
tts_client = texttospeech.TextToSpeechClient()

# Define database models (No changes here, keeping for context)
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

# Routes (No changes here, keeping for context)
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
        
        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email already registered')
            return redirect(url_for('register'))
        
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
        
        if credits_amount > 0:
            payment = Payment(
                user_id=current_user.id,
                amount=credits_amount * 2.0,  # Example: $2 per credit
                credits=credits_amount,
                payment_id=f"sim_{secrets.token_hex(8)}"  # Simulated payment ID
            )
            
            current_user.credits += credits_amount
            
            db.session.add(payment)
            db.session.commit()
            
            flash(f'Successfully purchased {credits_amount} credits')
            return redirect(url_for('dashboard'))
            
    return render_template('purchase.html')


# --- NEW: Global System Prompt for Gemini (Detailed Instructions) ---
INTERVIEWER_SYSTEM_PROMPT = """
ROLE: Expert virtual interviewer conducting professional job interviews.

TONE: Professional, encouraging, patient.

TASK:
1. Begin by asking the candidate to choose one of the following categories: "Computer Science", "Python and Web Development", or "HR".
2. Once the candidate chooses a category, conduct the interview strictly according to the structured flow defined for that category.
3. Interview flow for each category includes:
    - ICE BREAKER phase (first 1-2 questions) to warm up the candidate.
    - CORE TECHNICAL or BEHAVIORAL phase (minimum 5 questions) with progressively harder questions.
    - CLOSING question to wrap up the session.

INTERVIEW RULES:
- Ask only one question at a time.
- Wait for the candidate’s full response before continuing.
- If the answer is incomplete or unclear, ask "Could you elaborate?" or "Can you provide more details?"
- Never provide answers, suggestions, or corrections directly. Your role is to ask questions.
- Adjust question difficulty from easy to hard based on candidate’s responses and depth of understanding. Use internal evaluation criteria to guide question difficulty and follow-ups, without explicitly mentioning them.
- Maintain a coherent and natural conversation flow within the chosen interview category.
- If the user explicitly says words like "end interview", "that's all for now", or "bye", acknowledge their request to conclude the interview and state that you will generate a report.
"""

CATEGORY_DETAILS_PROMPT = """
CATEGORY INTERVIEW PATHS:

1. Computer Science
    - Ice Breaker:
        * "Tell me about your most challenging project"
        * "What interests you about distributed systems?"
    - Core Questions:
        * Conceptual questions on algorithms, time complexity, system design (e.g., "Describe a load balancer's role and types.", "Explain the CAP theorem with examples.", "How would you handle race conditions in a multi-threaded application?")
        * Coding problems with test cases (e.g., "Implement a function to find the Nth Fibonacci number recursively and iteratively. Analyze time complexity.", "Given an array of integers, return indices of the two numbers such that they add up to a specific target. Explain your approach and complexity.")
        * Thread-safe cache implementation example (e.g., "Design a thread-safe LRU cache. Discuss concurrency considerations and data structures involved.")
    - Closing:
        * "Do you have questions about our engineering culture?"

2. Python and Web Development
    - Ice Breaker:
        * "Explain decorators with a real-world example"
        * "How would you scale a Flask API to handle 1 million users?"
    - Core Questions:
        * Debugging short code snippets (e.g., "Analyze this Python snippet for potential errors or inefficiencies and suggest fixes.", "What does `asyncio` do and when would you use it in web development?")
        * Improving database queries (e.g., "Given a slow SQL query in a Django/Flask application, how would you optimize it? Consider ORM and raw SQL approaches.")
        * Designing secure rate-limited microservices (e.g., "Outline the design of a rate-limited authentication microservice using Python. Discuss security best practices.")
    - Closing:
        * "What Python or web development trends excite you, and why?"

3. HR Interview
    - Ice Breaker:
        * "Walk me through your career journey and what led you to this point."
    - Behavioral Questions (STAR format - Situation, Task, Action, Result):
        * "Describe a time you faced a conflict with a colleague. How did you handle it?"
        * "Give an example of a time you worked under tight deadlines. How did you ensure quality?"
        * "What would your previous manager say you could improve, and what steps have you taken to address it?"
        * "Tell me about a time you had to adapt to a significant change at work."
        * "Describe a project where you demonstrated strong leadership skills."
    - Closing:
        * "What questions do you have about our company values, culture, or this role?"
"""

# The full context for Gemini is built from these parts and stored in session
FULL_GEMINI_CONTEXT_PROMPT = INTERVIEWER_SYSTEM_PROMPT + "\n\n" + CATEGORY_DETAILS_PROMPT

# The very first question the AI asks the user
FIRST_USER_FACING_QUESTION = "Hello! I'm your AI interviewer. To begin, please choose a category for your interview: Computer Science, Python and Web Development, or HR."

# Socket.IO event handlers
@socketio.on('start_interview')
def handle_start_interview():
    if not current_user.is_authenticated:
        emit('error', {'message': 'Authentication required. Please log in.'}, room=request.sid)
        return
    
    if current_user.credits <= 0:
        emit('error', {'message': 'You do not have enough credits for an interview. Please purchase more.'}, room=request.sid)
        return

    app.logger.info(f"User {current_user.email} starting interview.")
    
    # Initialize conversation history with the detailed system prompt for Gemini
    # Each new interview starts with this foundational context for the AI
    session['conversation_history'] = [
        {"role": "user", "parts": [FULL_GEMINI_CONTEXT_PROMPT]},
        {"role": "model", "parts": ["Okay, I understand my role and the detailed interview structure for each category. I am ready to begin the interview process."]}]
    
    session['interview_active'] = True # Flag to indicate an interview is in progress
    session['interview_state'] = 'awaiting_category_selection' # New state: waiting for category choice
    session['interview_category'] = None # To store the chosen category
    session['start_time'] = datetime.utcnow().isoformat() # For potential credit tracking later
    
    try:
        initial_ai_response_text = FIRST_USER_FACING_QUESTION
        initial_ai_response_audio_base64 = _get_audio_from_text(initial_ai_response_text)
        
        # Add AI's initial question to history
        session['conversation_history'].append({"role": "model", "parts": [initial_ai_response_text]})
        
        emit('interview_started', {'message': 'Interview started'})
        emit('interviewer_message', {
            'text': initial_ai_response_text,
            'audio': initial_ai_response_audio_base64
        }, room=request.sid)
        
    except Exception as e:
        app.logger.error(f"Error during initial interview message: {e}")
        emit('error', {'message': f'Failed to start interview due to an AI error: {e}'}, room=request.sid)

@socketio.on('user_message')
def handle_user_message(data):
    if not current_user.is_authenticated:
        emit('error', {'message': 'Authentication required'}, room=request.sid)
        return
    
    if not session.get('interview_active', False):
        emit('error', {'message': 'No active interview session. Please start a new interview.'}, room=request.sid)
        return

    user_text = data.get('text', '').strip()
    app.logger.info(f"User {current_user.email} message: {user_text}")

    if not user_text:
        emit('error', {'message': 'Empty message received.'}, room=request.sid)
        return
    
    # Check for interview end command
    if re.search(r'\b(end|bye|finish|that\'s all)\b', user_text.lower()):
        # Acknowledge user's request to end the interview
        end_message_text = "Thank you for your time. This concludes our interview. I will now generate your report."
        end_message_audio = _get_audio_from_text(end_message_text)
        
        emit('interviewer_message', {
            'text': end_message_text,
            'audio': end_message_audio
        }, room=request.sid)
        
        # Let the frontend play the audio, then trigger report generation after a short delay
        # Client will emit 'interview_ended_acknowledged' after playing audio
        return

    conversation_history_for_session = session.get('conversation_history', [])
    
    # --- Category Selection Logic ---
    if session.get('interview_state') == 'awaiting_category_selection':
        chosen_category = None
        user_text_lower = user_text.lower()
        if "computer science" in user_text_lower or "cs" in user_text_lower:
            chosen_category = "Computer Science"
        elif "python" in user_text_lower or "web development" in user_text_lower or "web dev" in user_text_lower:
            chosen_category = "Python and Web Development"
        elif "hr" in user_text_lower or "human resources" in user_text_lower:
            chosen_category = "HR Interview" # Using "HR Interview" to match prompt for clarity
        
        if chosen_category:
            session['interview_category'] = chosen_category
            session['interview_state'] = 'interview_in_progress'
            app.logger.info(f"User selected category: {chosen_category}")
            
            # Now, prompt Gemini to ask the *first ice-breaker* for the chosen category
            # Add user's choice to history so Gemini knows the context
            conversation_history_for_session.append({"role": "user", "parts": [f"I choose the {chosen_category} category. Please begin the interview with an ice-breaker question for this category."]})
            session['conversation_history'] = conversation_history_for_session
            
            try:
                chat_session = gemini_model.start_chat(history=conversation_history_for_session)
                ai_response = chat_session.send_message("Initiate the interview based on my chosen category.")
                
                ai_response_text = ai_response.text.strip()
                if not ai_response_text:
                    ai_response_text = "I'm sorry, I couldn't generate the first question for your chosen category. Could you please rephrase your choice?"
                
                ai_response_audio_base64 = _get_audio_from_text(ai_response_text)
                
                conversation_history_for_session.append({"role": "model", "parts": [ai_response_text]})
                session['conversation_history'] = conversation_history_for_session
                
                emit('interviewer_message', {
                    'text': ai_response_text,
                    'audio': ai_response_audio_base64
                }, room=request.sid)
                
            except Exception as e:
                app.logger.error(f"Error starting interview after category selection: {e}")
                emit('error', {'message': f'Failed to start interview: {e}'}, room=request.sid)
            return
        else:
            # If category not understood, ask again
            ai_response_text = "I didn't quite catch that. Please choose one of the following categories: Computer Science, Python and Web Development, or HR."
            ai_response_audio_base64 = _get_audio_from_text(ai_response_text)
            emit('interviewer_message', {
                'text': ai_response_text,
                'audio': ai_response_audio_base64
            }, room=request.sid)
            return

    # --- Interview In Progress Logic ---
    # Add user message to conversation history for ongoing interview
    conversation_history_for_session.append({"role": "user", "parts": [user_text]})
    session['conversation_history'] = conversation_history_for_session # Update session

    try:
        # Generate AI response using Gemini
        # Pass the entire conversation history including the system prompt
        chat_session = gemini_model.start_chat(history=conversation_history_for_session)
        ai_response = chat_session.send_message(user_text) # Send the latest user message
        
        # Extract the text from AI response
        ai_response_text = ai_response.text.strip()
        
        if not ai_response_text:
            ai_response_text = "I'm sorry, I couldn't generate a response. Could you please rephrase?"
            app.logger.warning(f"Gemini returned empty response for user: {user_text}")
        
        # Add AI response to conversation history
        conversation_history_for_session.append({"role": "model", "parts": [ai_response_text]})
        session['conversation_history'] = conversation_history_for_session # Update session

        # Generate audio for AI response
        ai_response_audio_base64 = _get_audio_from_text(ai_response_text)

        emit('interviewer_message', {
            'text': ai_response_text,
            'audio': ai_response_audio_base64
        }, room=request.sid)

    except Exception as e:
        app.logger.error(f"Error during AI response generation: {e}")
        emit('error', {'message': f'Failed to get AI response: {e}'}, room=request.sid)


@socketio.on('interview_ended_acknowledged')
def handle_interview_ended_acknowledged():
    # This event is emitted by the client after it finishes playing the "interview ended" audio.
    app.logger.info(f"User {current_user.email} acknowledged interview end. Generating report.")
    complete_interview()


# --- NEW: Helper function for TTS ---
def _get_audio_from_text(text):
    # This function uses Google Cloud Text-to-Speech
    # Select the language and voice
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-IN", # Or "en-US", "en-GB" - ensure this matches desired voice
        name="en-IN-Neural2-D", # Example: a high-quality Neural2 voice, check available voices
        ssml_gender=texttospeech.SsmlVoiceGender.FEMALE # Or MALE, NEUTRAL
    )

    # Select the type of audio file you want
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    synthesis_input = texttospeech.SynthesisInput(text=text)

    # Perform the text-to-speech request
    response = tts_client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    
    # Return audio content as base64 string for embedding in JSON/WebSocket
    # For large audio files, consider streaming or separate endpoint
    import base64
    return base64.b64encode(response.audio_content).decode('utf-8')


def complete_interview():
    """
    Function to end the interview and generate a report
    """
    if not current_user.is_authenticated:
        app.logger.error("Attempted to complete interview for unauthenticated user.")
        return # Should not happen if flow is correct

    # Retrieve conversation history from session
    final_conversation = session.pop('conversation_history', [])
    session['interview_active'] = False # End the interview session
    session['interview_state'] = 'inactive' # Set state to inactive
    session['interview_category'] = None # Clear category

    # --- NEW: Generate a more detailed report with LLM ---
    # This will consume a bit more tokens but creates a valuable feature
    report_summary_prompt = f"""Based on the following interview conversation, generate a detailed interview report.
    The interview was conducted for the category: {session.get('interview_category', 'unknown')}.
    The report should include:
    - Overall assessment (e.g., "Good", "Needs Improvement")
    - Strengths observed in the candidate.
    - Areas for improvement, with specific examples from the conversation if possible.
    - Detailed feedback on communication, content of answers, and delivery.
    - Do NOT include the full transcript in this generated summary.
    Format the output as a JSON object with keys: "overall_assessment", "strengths", "areas_for_improvement", "detailed_feedback".
    Detailed feedback should be an object with keys "communication", "content", "delivery".

    Interview Conversation:
    {json.dumps(final_conversation, indent=2)}
    """

    report_content = {
        'conversation_id': session.get('interview_id', 'unknown'),
        'overall': 'N/A', # Placeholder until LLM generates
        'strengths': 'N/A',
        'improvement': 'N/A',
        'detailed_feedback': {},
        'full_conversation': final_conversation # Store full conversation for debugging/detailed lookup
    }

    try:
        # Use LLM to generate report summary
        report_llm_response = gemini_model.generate_content(report_summary_prompt)
        report_text = report_llm_response.text.strip()
        
        # Attempt to parse the LLM's response as JSON
        parsed_report_data = json.loads(report_text)
        
        report_content['overall'] = parsed_report_data.get('overall_assessment', 'N/A')
        report_content['strengths'] = parsed_report_data.get('strengths', 'N/A')
        report_content['improvement'] = parsed_report_data.get('areas_for_improvement', 'N/A')
        report_content['detailed_feedback'] = parsed_report_data.get('detailed_feedback', {})

    except Exception as e:
        app.logger.error(f"Error generating LLM report: {e}. LLM response: {report_text[:200] if 'report_text' in locals() else 'N/A'}")
        report_content['overall'] = "Error in report generation"
        report_content['detailed_feedback'] = {"error": str(e), "llm_raw_response": report_text if 'report_text' in locals() else 'N/A'}


    # Create a new report
    try:
        report = Report(user_id=current_user.id, content=report_content)
        
        # Deduct credit only if it's the *first* interview instance (or based on duration)
        if current_user.deduct_credit():
            app.logger.info(f"Credit deducted for user {current_user.email}. Remaining credits: {current_user.credits}")
        else:
            app.logger.warning(f"Credit deduction failed for user {current_user.email}. Credits: {current_user.credits}")
            # Consider emitting an error to user here if credit deduction is critical
            
        db.session.add(report)
        db.session.commit()
        
        socketio.emit('interview_completed', {'success': True, 'report_id': report.id}, room=request.sid)
        
    except Exception as e:
        app.logger.error(f"Error creating report: {str(e)}")
        socketio.emit('error', {'message': 'Error creating report'}, room=request.sid)

# Create database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # For production, replace debug=True with False
    # If using a WSGI server like Gunicorn/gevent/eventlet, configure async_mode in SocketIO
    # For simple local dev, default threading is fine.
    socketio.run(app, debug=True, host='0.0.0.0', port=port)
