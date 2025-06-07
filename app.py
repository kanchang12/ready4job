from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
from werkzeug.utils import secure_filename
import PyPDF2
import requests
from datetime import datetime
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
import razorpay
import hmac
import hashlib

load_dotenv()

app = Flask(__name__)

# Use EXACT .env variables
FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
ELEVENLABS_AGENT_ID = os.getenv('ELEVENLABS_AGENT_ID')
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')

app.secret_key = FLASK_SECRET_KEY

# Supabase client
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
except:
    supabase = None

# Razorpay client
try:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET else None
except:
    razorpay_client = None

# File upload configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(file_path):
    """Extract text from PDF CV"""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        print(f"Error extracting PDF text: {e}")
        return None

def get_user_credits(user_id):
    """Get user's current credit balance"""
    try:
        if not supabase:
            return 5
            
        result = supabase.table('user_credits').select('credits').eq('user_id', user_id).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]['credits']
        return 0
    except Exception as e:
        print(f"Error getting user credits: {e}")
        return 0

def add_credits(user_id, credits_to_add):
    """Add credits to user's balance"""
    try:
        if not supabase:
            return True
            
        current_credits = get_user_credits(user_id)
        new_credits = current_credits + credits_to_add
        
        result = supabase.table('user_credits').update({
            'credits': new_credits,
            'updated_at': datetime.now().isoformat()
        }).eq('user_id', user_id).execute()
        
        return bool(result.data)
    except Exception as e:
        print(f"Error adding credits: {e}")
        return False

def deduct_credit(user_id):
    """Deduct one credit from user's balance"""
    try:
        if not supabase:
            return True
            
        current_credits = get_user_credits(user_id)
        if current_credits <= 0:
            return False
        
        new_credits = current_credits - 1
        supabase.table('user_credits').update({
            'credits': new_credits,
            'updated_at': datetime.now().isoformat()
        }).eq('user_id', user_id).execute()
        return True
    except Exception as e:
        print(f"Error deducting credit: {e}")
        return False

@app.route('/test/add_credits', methods=['POST'])
def test_add_credits():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        credits_to_add = data.get('credits', 10)
        
        if add_credits(session['user_id'], credits_to_add):
            return jsonify({
                'message': f'Added {credits_to_add} credits successfully',
                'new_balance': get_user_credits(session['user_id'])
            }), 200
        else:
            return jsonify({'error': 'Failed to add credits'}), 500
            
    except Exception as e:
        print(f"Error adding test credits: {e}")
        return jsonify({'error': 'Failed to add credits'}), 500

def create_interview_record(user_id, title, cv_text, job_role=None):
    """Create interview record in database with 'started' status"""
    try:
        if not supabase:
            return str(uuid.uuid4())
        
        # First, try to get the table schema to check if job_role column exists
        try:
            # Test query to check if job_role column exists
            test_result = supabase.table('interviews').select('job_role').limit(1).execute()
            has_job_role_column = True
        except:
            has_job_role_column = False
            print("Warning: job_role column not found in interviews table")
        
        # Create interview data based on available columns
        interview_data = {
            'user_id': user_id,
            'title': title,
            'cv_text': cv_text,
            'status': 'started',
            'transcript': [],
            'created_at': datetime.now().isoformat()
        }
        
        # Only add job_role if the column exists
        if has_job_role_column and job_role:
            interview_data['job_role'] = job_role
        
        result = supabase.table('interviews').insert(interview_data).execute()
        return result.data[0]['id'] if result.data else None
    except Exception as e:
        print(f"Error creating interview record: {e}")
        return None

def update_interview_status(interview_id, status, conversation_id=None, transcript=None):
    """Update interview status and add conversation data"""
    try:
        if not supabase:
            return True
            
        update_data = {
            'status': status,
            'updated_at': datetime.now().isoformat()
        }
        
        if conversation_id:
            update_data['conversation_id'] = conversation_id
            
        if transcript:
            update_data['transcript'] = transcript
            
        if status == 'completed':
            update_data['completed_at'] = datetime.now().isoformat()
        
        result = supabase.table('interviews').update(update_data).eq('id', interview_id).execute()
        return bool(result.data)
    except Exception as e:
        print(f"Error updating interview status: {e}")
        return False

# Updated function to create ElevenLabs conversation with better error handling
def create_elevenlabs_conversation(cv_text, job_role=None):
    """Create a conversation with ElevenLabs - improved version"""
    try:
        if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID:
            print("ElevenLabs credentials not configured")
            return None
            
        # Prepare enhanced context for the AI interviewer
        context = f"""You are conducting a professional job interview. 

CANDIDATE'S CV:
{cv_text}

"""
        if job_role:
            context += f"POSITION APPLYING FOR: {job_role}\n\n"
            
        context += """INTERVIEW INSTRUCTIONS:
- Ask relevant questions based on their CV and experience
- Be professional, encouraging, and thorough
- Probe deeper into their technical skills and projects
- Ask about specific experiences mentioned in their CV
- Include behavioral questions about teamwork, challenges, etc.
- Keep the conversation natural and flowing
- End with asking if they have any questions for you

Start by greeting them and asking them to introduce themselves briefly."""
        
        headers = {
            'xi-api-key': ELEVENLABS_API_KEY,
            'Content-Type': 'application/json'
        }
        
        # Enhanced payload for better conversation
        payload = {
            'agent_id': ELEVENLABS_AGENT_ID,
            'context': context,
            'settings': {
                'voice_settings': {
                    'stability': 0.8,
                    'similarity_boost': 0.75
                }
            }
        }
        
        response = requests.post(
            'https://api.elevenlabs.io/v1/convai/conversations',
            headers=headers,
            json=payload,
            timeout=30  # Add timeout
        )
        
        if response.status_code == 201:
            conversation_data = response.json()
            return conversation_data.get('conversation_id')
        else:
            print(f"ElevenLabs API error: {response.status_code} - {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Network error creating ElevenLabs conversation: {e}")
        return None
    except Exception as e:
        print(f"Error creating ElevenLabs conversation: {e}")
        return None

# Enhanced function to get conversation transcript
def get_conversation_transcript(conversation_id):
    """Get transcript from ElevenLabs conversation with better error handling"""
    try:
        if not ELEVENLABS_API_KEY:
            return []
            
        headers = {
            'xi-api-key': ELEVENLABS_API_KEY
        }
        
        response = requests.get(
            f'https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}',
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('transcript', [])
        else:
            print(f"Error getting transcript: {response.status_code} - {response.text}")
            return []
            
    except requests.exceptions.RequestException as e:
        print(f"Network error getting transcript: {e}")
        return []
    except Exception as e:
        print(f"Error getting conversation transcript: {e}")
        return []

def create_purchase_record(user_id, amount, credits_purchased, transaction_id=None):
    """Create purchase record in database"""
    try:
        if not supabase:
            return str(uuid.uuid4())
            
        purchase_data = {
            'user_id': user_id,
            'amount': float(amount),
            'credits_purchased': credits_purchased,
            'payment_method': 'razorpay',
            'transaction_id': transaction_id,
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }
        
        result = supabase.table('purchases').insert(purchase_data).execute()
        return result.data[0]['id'] if result.data else None
    except Exception as e:
        print(f"Error creating purchase record: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        full_name = data.get('name')
        
        if not all([email, password, full_name]):
            return jsonify({'error': 'All fields are required'}), 400
        
        if not supabase:
            return jsonify({'error': 'Database not configured'}), 500
        
        auth_response = supabase.auth.sign_up({
            'email': email,
            'password': password,
            'options': {
                'data': {
                    'full_name': full_name
                }
            }
        })
        
        if auth_response.user:
            user_id = auth_response.user.id
            
            user_data = {
                'id': user_id,
                'email': email,
                'full_name': full_name,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('users').insert(user_data).execute()
            
            credits_data = {
                'user_id': user_id,
                'credits': 3,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('user_credits').insert(credits_data).execute()
            
            return jsonify({'message': 'Registration successful!'}), 201
        else:
            return jsonify({'error': 'Registration failed'}), 400
            
    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/login', methods=['POST', 'GET'])
def login():
    if request.method == 'GET':
        return redirect(url_for('index'))
        
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
        
        if not supabase:
            return jsonify({'error': 'Database not configured'}), 500
        
        auth_response = supabase.auth.sign_in_with_password({
            'email': email,
            'password': password
        })
        
        if auth_response.user:
            session['user_id'] = auth_response.user.id
            session['user_email'] = auth_response.user.email
            
            user_profile = supabase.table('users').select('full_name').eq('id', auth_response.user.id).execute()
            if user_profile.data and len(user_profile.data) > 0:
                session['user_name'] = user_profile.data[0].get('full_name', 'User')
            else:
                session['user_name'] = 'User'
            
            return jsonify({
                'message': 'Login successful',
                'user_id': auth_response.user.id,
                'redirect': '/dashboard'
            }), 200
        else:
            return jsonify({'error': 'Invalid credentials'}), 401
            
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Login failed'}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    
    try:
        # Get fresh data
        credit_result = supabase.table('user_credits').select('credits').eq('user_id', user_id).execute()
        credits = credit_result.data[0]['credits'] if credit_result.data else 0
        
        interview_result = supabase.table('interviews').select('*').eq('user_id', user_id).execute()
        total_interviews = len(interview_result.data) if interview_result.data else 0
        
        completed_interviews = [i for i in interview_result.data if i['status'] == 'completed'] if interview_result.data else []
        avg_score = sum(i.get('score', 0) for i in completed_interviews if i.get('score')) / len(completed_interviews) if completed_interviews else 0
        
        stats = {
            'credits': credits,
            'total_interviews': total_interviews,
            'avg_score': round(avg_score, 1) if avg_score > 0 else 'N/A',
            'last_interview': completed_interviews[-1]['created_at'][:10] if completed_interviews else 'Never'
        }
        
    except Exception as e:
        print(f"Dashboard error: {e}")
        stats = {'credits': 0, 'total_interviews': 0, 'avg_score': 'N/A', 'last_interview': 'Never'}
    
    return render_template('dashboard.html', user_name=session.get('user_name', 'User'), stats=stats)

@app.route('/interview')
def interview():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    credits = get_user_credits(session['user_id'])
    # Pass ElevenLabs config to template
    return render_template('interview.html', 
                         credits=credits, 
                         config={'ELEVENLABS_AGENT_ID': ELEVENLABS_AGENT_ID})

@app.route('/start_interview', methods=['POST'])
def start_interview():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    if get_user_credits(user_id) < 1:
        return jsonify({'error': 'Insufficient credits'}), 400
    
    try:
        title = request.form.get('title', 'Interview Session')
        job_role = request.form.get('job_role', '')
        
        if 'cv_file' not in request.files:
            return jsonify({'error': 'No CV file uploaded'}), 400
        
        file = request.files['cv_file']
        if file.filename == '' or not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file. Please upload a PDF.'}), 400
        
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{user_id}_{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        cv_text = extract_text_from_pdf(file_path)
        if not cv_text:
            os.remove(file_path)
            return jsonify({'error': 'Could not extract text from PDF'}), 400
        
        # Create interview record with 'started' status (only pass job_role if it's not empty)
        interview_id = create_interview_record(user_id, title, cv_text, job_role if job_role else None)
        if not interview_id:
            os.remove(file_path)
            return jsonify({'error': 'Failed to create interview record'}), 500
        
        # Deduct credit only after successful interview creation
        if not deduct_credit(user_id):
            os.remove(file_path)
            return jsonify({'error': 'Failed to deduct credit'}), 500
        
        os.remove(file_path)
        
        return jsonify({
            'message': 'CV processed successfully! Ready to start interview.',
            'interview_id': interview_id,
            'cv_text': cv_text,
            'job_role': job_role,
            'credits_remaining': get_user_credits(user_id)
        }), 200
        
    except Exception as e:
        print(f"Error starting interview: {e}")
        return jsonify({'error': 'Failed to start interview'}), 500

@app.route('/create_conversation', methods=['POST'])
def create_conversation():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        interview_id = data.get('interview_id')
        cv_text = data.get('cv_text', '')
        job_role = data.get('job_role', '')
        
        if not interview_id:
            return jsonify({'error': 'Interview ID required'}), 400
        
        # Verify interview belongs to user
        user_id = session['user_id']
        if supabase:
            try:
                # Try to get all interview data
                interview_check = supabase.table('interviews').select('*').eq('id', interview_id).eq('user_id', user_id).execute()
                if not interview_check.data:
                    return jsonify({'error': 'Interview not found'}), 404
                
                # Get job_role from database if available
                interview_data = interview_check.data[0]
                stored_job_role = interview_data.get('job_role')
                if stored_job_role:
                    job_role = stored_job_role
                    
            except Exception as e:
                print(f"Error fetching interview data: {e}")
                # Just verify the interview exists
                interview_check = supabase.table('interviews').select('id').eq('id', interview_id).eq('user_id', user_id).execute()
                if not interview_check.data:
                    return jsonify({'error': 'Interview not found'}), 404
        
        # Create ElevenLabs conversation or simulate it
        conversation_id = None
        
        if ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID:
            # Try to create real ElevenLabs conversation
            conversation_id = create_elevenlabs_conversation(cv_text, job_role)
            
        if conversation_id:
            # Real ElevenLabs conversation created
            update_interview_status(interview_id, 'in_progress', conversation_id)
            
            return jsonify({
                'message': 'Voice interview started successfully',
                'conversation_id': conversation_id,
                'type': 'elevenlabs'
            }), 200
        else:
            # Simulate conversation for demo
            simulated_conversation_id = f"sim_{uuid.uuid4().hex[:8]}"
            update_interview_status(interview_id, 'in_progress', simulated_conversation_id)
            
            return jsonify({
                'message': 'Voice interview started (demo mode)',
                'conversation_id': simulated_conversation_id,
                'type': 'demo'
            }), 200
        
    except Exception as e:
        print(f"Error creating conversation: {e}")
        return jsonify({'error': 'Failed to start voice interview'}), 500

@app.route('/end_interview', methods=['POST'])
def end_interview():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        conversation_id = data.get('conversation_id')
        interview_id = data.get('interview_id')
        
        if not conversation_id or not interview_id:
            return jsonify({'error': 'Conversation ID and Interview ID required'}), 400
        
        # Verify interview belongs to user
        user_id = session['user_id']
        if supabase:
            interview_check = supabase.table('interviews').select('*').eq('id', interview_id).eq('user_id', user_id).execute()
            if not interview_check.data:
                return jsonify({'error': 'Interview not found'}), 404
        
        # Get transcript from ElevenLabs (if real conversation)
        transcript = []
        if not conversation_id.startswith('sim_'):
            transcript = get_conversation_transcript(conversation_id)
        else:
            # Simulated transcript for demo
            transcript = [
                {"speaker": "AI", "message": "Hello! Thank you for joining today's interview. Can you start by telling me about yourself?"},
                {"speaker": "User", "message": "Thank you for having me. I'm a software developer with 3 years of experience..."},
                {"speaker": "AI", "message": "That's great! Can you tell me about a challenging project you've worked on?"},
                {"speaker": "User", "message": "Sure, I recently worked on a microservices architecture project..."}
            ]
        
        # Update interview status to completed
        update_interview_status(interview_id, 'completed', conversation_id, transcript)
        
        return jsonify({
            'message': 'Interview ended successfully',
            'transcript_length': len(transcript)
        }), 200
        
    except Exception as e:
        print(f"Error ending interview: {e}")
        return jsonify({'error': 'Failed to end interview'}), 500

@app.route('/get_credits')
def get_credits():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    credits = get_user_credits(session['user_id'])
    return jsonify({'credits': credits})

# Replace the create_order route in your app.py

@app.route('/create_order', methods=['POST'])
def create_razorpay_order():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        credits_package = data.get('credits')
        
        packages = {
            10: {'amount': 9900, 'credits': 10},   # ₹99
            25: {'amount': 19900, 'credits': 25},  # ₹199  
            50: {'amount': 34900, 'credits': 50}   # ₹349
        }
        
        if credits_package not in packages:
            return jsonify({'error': 'Invalid package'}), 400
        
        package = packages[credits_package]
        
        # Check if Razorpay is configured
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
            print("Razorpay not configured - using demo mode")
            # Demo mode - simulate successful order
            demo_order_id = f"demo_order_{int(datetime.now().timestamp())}"
            return jsonify({
                'order_id': demo_order_id,
                'amount': package['amount'],
                'currency': 'INR',
                'key_id': 'demo_key',
                'demo_mode': True,
                'credits': package['credits']
            }), 200
        
        if not razorpay_client:
            return jsonify({'error': 'Payment system initialization failed'}), 500
        
        # Create short receipt (max 40 chars)
        timestamp = int(datetime.now().timestamp())
        user_short_id = session['user_id'][:8]  # First 8 chars of user ID
        receipt = f"ord_{user_short_id}_{timestamp}"[:40]  # Ensure max 40 chars
        
        order_data = {
            'amount': package['amount'],  # Amount in paise
            'currency': 'INR',
            'receipt': receipt,
            'notes': {
                'user_id': session['user_id'],
                'credits': package['credits']
            }
        }
        
        print(f"Creating Razorpay order: {order_data}")
        order = razorpay_client.order.create(data=order_data)
        print(f"Razorpay order created: {order}")
        
        # Create purchase record
        purchase_id = create_purchase_record(
            session['user_id'], 
            package['amount'] / 100,  # Convert paise to rupees
            package['credits'],
            order['id']
        )
        
        return jsonify({
            'order_id': order['id'],
            'amount': package['amount'],
            'currency': 'INR',
            'key_id': RAZORPAY_KEY_ID,
            'purchase_id': purchase_id
        }), 200
        
    except Exception as e:
        print(f"Error creating Razorpay order: {e}")
        return jsonify({'error': f'Failed to create order: {str(e)}'}), 500

        
def verify_razorpay_payment():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id') 
        razorpay_signature = data.get('razorpay_signature')
        
        # Handle demo mode
        if razorpay_order_id and razorpay_order_id.startswith('demo_order_'):
            credits_to_add = data.get('credits', 10)  # Default to 10 credits for demo
            
            if add_credits(session['user_id'], credits_to_add):
                return jsonify({
                    'message': 'Demo payment successful',
                    'credits_added': credits_to_add,
                    'new_balance': get_user_credits(session['user_id']),
                    'demo_mode': True
                }), 200
            else:
                return jsonify({'error': 'Failed to add credits in demo mode'}), 500
        
        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return jsonify({'error': 'Missing payment data'}), 400
        
        if not RAZORPAY_KEY_SECRET:
            return jsonify({'error': 'Payment verification not configured'}), 500
        
        # Verify signature
        generated_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode('utf-8'),
            f"{razorpay_order_id}|{razorpay_payment_id}".encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if generated_signature != razorpay_signature:
            return jsonify({'error': 'Invalid payment signature'}), 400
        
        if razorpay_client:
            try:
                order = razorpay_client.order.fetch(razorpay_order_id)
                credits_to_add = int(order['notes']['credits'])
                
                if add_credits(session['user_id'], credits_to_add):
                    # Update purchase record
                    if supabase:
                        supabase.table('purchases').update({
                            'status': 'completed',
                            'transaction_id': razorpay_payment_id,
                            'updated_at': datetime.now().isoformat()
                        }).eq('transaction_id', razorpay_order_id).execute()
                    
                    return jsonify({
                        'message': 'Payment successful',
                        'credits_added': credits_to_add,
                        'new_balance': get_user_credits(session['user_id'])
                    }), 200
                else:
                    return jsonify({'error': 'Failed to add credits'}), 500
                    
            except Exception as e:
                print(f"Error fetching Razorpay order: {e}")
                return jsonify({'error': 'Failed to verify payment with Razorpay'}), 500
        else:
            return jsonify({'error': 'Payment system not configured'}), 500
            
    except Exception as e:
        print(f"Error verifying payment: {e}")
        return jsonify({'error': 'Payment verification failed'}), 500

@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    try:
        if supabase:
            interviews = supabase.table('interviews').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
            interview_list = interviews.data if interviews.data else []
        else:
            interview_list = []
        return render_template('history.html', interviews=interview_list)
    except Exception as e:
        print(f"Error getting interview history: {e}")
        return render_template('history.html', interviews=[])

@app.route('/review/<interview_id>')
def review(interview_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    try:
        if supabase:
            interview = supabase.table('interviews').select('*').eq('id', interview_id).eq('user_id', user_id).execute()
            if interview.data and len(interview.data) > 0:
                return render_template('review.html', interview=interview.data[0])
        return redirect(url_for('history'))
    except Exception as e:
        print(f"Error getting interview review: {e}")
        return redirect(url_for('history'))

@app.route('/delete_interview/<interview_id>', methods=['DELETE'])
def delete_interview(interview_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    try:
        if supabase:
            # Verify interview belongs to user
            interview_check = supabase.table('interviews').select('id').eq('id', interview_id).eq('user_id', user_id).execute()
            if not interview_check.data:
                return jsonify({'error': 'Interview not found'}), 404
            
            # Delete the interview
            result = supabase.table('interviews').delete().eq('id', interview_id).eq('user_id', user_id).execute()
            if result.data:
                return jsonify({'message': 'Interview deleted successfully'}), 200
            else:
                return jsonify({'error': 'Failed to delete interview'}), 500
        else:
            return jsonify({'error': 'Database not configured'}), 500
    except Exception as e:
        print(f"Error deleting interview: {e}")
        return jsonify({'error': 'Failed to delete interview'}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)