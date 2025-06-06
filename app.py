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
import json
from datetime import datetime
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

def create_interview_record(user_id, title, cv_text, job_role=None):
    """Create interview record in database with 'started' status"""
    try:
        if not supabase:
            return str(uuid.uuid4())
        
        interview_data = {
            'user_id': user_id,
            'title': title,
            'cv_text': cv_text,
            'status': 'started',
            'transcript': [],
            'created_at': datetime.now().isoformat()
        }
        
        if job_role:
            interview_data['job_role'] = job_role
        
        result = supabase.table('interviews').insert(interview_data).execute()
        return result.data[0]['id'] if result.data else None
    except Exception as e:
        print(f"Error creating interview record: {e}")
        return None

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

# ROUTES

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
    return render_template('interview.html', 
                         credits=credits, 
                         config={'ELEVENLABS_AGENT_ID': ELEVENLABS_AGENT_ID,
                                'ELEVENLABS_API_KEY': ELEVENLABS_API_KEY})

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
        
        interview_id = create_interview_record(user_id, title, cv_text, job_role if job_role else None)
        if not interview_id:
            os.remove(file_path)
            return jsonify({'error': 'Failed to create interview record'}), 500
        
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
        conversation_id = data.get('conversation_id', f"conv_{uuid.uuid4().hex[:8]}")
        
        print(f"Creating conversation for interview: {interview_id}")
        
        if not interview_id:
            return jsonify({'error': 'Interview ID required'}), 400
        
        user_id = session['user_id']
        if supabase:
            try:
                result = supabase.table('interviews').update({
                    'status': 'in_progress',
                    'conversation_id': conversation_id,
                    'updated_at': datetime.now().isoformat()
                }).eq('id', interview_id).eq('user_id', user_id).execute()
                
                print(f"Updated interview status: {result}")
            except Exception as e:
                print(f"Database update error: {e}")
        
        return jsonify({
            'message': 'Voice interview started successfully',
            'conversation_id': conversation_id,
            'interview_id': interview_id,
            'type': 'elevenlabs'
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
        interview_id = data.get('interview_id')
        
        print(f"Ending interview: {interview_id}")
        
        if not interview_id:
            return jsonify({'error': 'Interview ID required'}), 400
        
        user_id = session['user_id']
        if supabase:
            try:
                result = supabase.table('interviews').update({
                    'status': 'completed',
                    'completed_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }).eq('id', interview_id).eq('user_id', user_id).execute()
                
                print(f"Interview completed: {result}")
            except Exception as e:
                print(f"Database update error: {e}")
        
        return jsonify({
            'message': 'Interview ended successfully',
            'status': 'completed'
        }), 200
        
    except Exception as e:
        print(f"Error ending interview: {e}")
        return jsonify({'error': 'Failed to end interview'}), 500

@app.route('/get_credits', methods=['GET'])
def get_credits():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        credits = get_user_credits(session['user_id'])
        return jsonify({'credits': credits}), 200
    except Exception as e:
        print(f"Error getting credits: {e}")
        return jsonify({'error': 'Failed to get credits'}), 500

@app.route('/create_order', methods=['POST'])
def create_razorpay_order():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        credits_package = data.get('credits')
        
        packages = {
            10: {'amount': 9900, 'credits': 10},
            25: {'amount': 19900, 'credits': 25},
            50: {'amount': 34900, 'credits': 50}
        }
        
        if credits_package not in packages:
            return jsonify({'error': 'Invalid package'}), 400
        
        package = packages[credits_package]
        
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
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
        
        timestamp = int(datetime.now().timestamp())
        user_short_id = session['user_id'][:8]
        receipt = f"ord_{user_short_id}_{timestamp}"[:40]
        
        order_data = {
            'amount': package['amount'],
            'currency': 'INR',
            'receipt': receipt,
            'notes': {
                'user_id': session['user_id'],
                'credits': package['credits']
            }
        }
        
        order = razorpay_client.order.create(data=order_data)
        
        purchase_id = create_purchase_record(
            session['user_id'], 
            package['amount'] / 100,
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

@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id') 
        razorpay_signature = data.get('razorpay_signature')
        
        if razorpay_order_id and razorpay_order_id.startswith('demo_order_'):
            credits_to_add = 10
            
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
        
        try:
            generated_signature = hmac.new(
                RAZORPAY_KEY_SECRET.encode('utf-8'),
                f"{razorpay_order_id}|{razorpay_payment_id}".encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            if generated_signature != razorpay_signature:
                return jsonify({'error': 'Invalid payment signature'}), 400
                
        except Exception as e:
            return jsonify({'error': 'Payment verification failed'}), 500
        
        if razorpay_client:
            try:
                order = razorpay_client.order.fetch(razorpay_order_id)
                credits_to_add = int(order.get('notes', {}).get('credits', 10))
                
                if add_credits(session['user_id'], credits_to_add):
                    try:
                        if supabase:
                            supabase.table('purchases').update({
                                'status': 'completed',
                                'transaction_id': razorpay_payment_id,
                                'updated_at': datetime.now().isoformat()
                            }).eq('transaction_id', razorpay_order_id).execute()
                    except Exception as e:
                        print(f"Error updating purchase record: {e}")
                    
                    return jsonify({
                        'message': f'Payment successful! {credits_to_add} credits added.',
                        'credits_added': credits_to_add,
                        'new_balance': get_user_credits(session['user_id'])
                    }), 200
                else:
                    return jsonify({'error': 'Failed to add credits to account'}), 500
                    
            except Exception as e:
                return jsonify({'error': 'Failed to verify payment with Razorpay'}), 500
        else:
            return jsonify({'error': 'Payment system not configured'}), 500
            
    except Exception as e:
        return jsonify({'error': 'Payment verification failed'}), 500

@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    interview_list = []
    supabase_connected = False
    
    try:
        print(f"History: Fetching interviews for user_id: {user_id}")
        
        if supabase:
            supabase_connected = True
            print("History: Supabase client is available")
            
            # Try to fetch interviews
            interviews = supabase.table('interviews').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
            
            print(f"History: Supabase query result: {interviews}")
            print(f"History: Interviews data: {interviews.data}")
            print(f"History: Number of interviews: {len(interviews.data) if interviews.data else 0}")
            
            if interviews.data:
                interview_list = interviews.data
                print(f"History: Successfully fetched {len(interview_list)} interviews")
                for i, interview in enumerate(interview_list):
                    print(f"History: Interview {i}: {interview}")
            else:
                print("History: No interviews found in database")
                
        else:
            print("History: Supabase client is None")
            
    except Exception as e:
        print(f"Error getting interview history: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"History: Final interview_list length: {len(interview_list)}")
    
    return render_template('history.html', 
                         interviews=interview_list,
                         supabase_connected=supabase_connected)

@app.route('/delete_interview/<interview_id>', methods=['DELETE'])
def delete_interview(interview_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    try:
        if supabase:
            interview_check = supabase.table('interviews').select('id').eq('id', interview_id).eq('user_id', user_id).execute()
            if not interview_check.data:
                return jsonify({'error': 'Interview not found'}), 404
            
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

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
