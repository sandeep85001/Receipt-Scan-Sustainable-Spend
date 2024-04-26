from flask import Flask, render_template, request, jsonify, redirect, url_for, session,send_file
import os
from werkzeug.utils import secure_filename
import boto3
import google.generativeai as genai
import uuid
from flask_cors import CORS
from boto3.dynamodb.conditions import Key, Attr
import tempfile

app = Flask(__name__)
CORS(app)
app.secret_key = 'sap_labs'

AWS_ACCESS_KEY_ID = 'ACCESS_KEY_ID'
AWS_SECRET_ACCESS_KEY = 'SECRET_ACCESS_KEY'
AWS_REGION = 'ap-south-1'
S3_BUCKET_NAME = 'receipt-scan-sustainable-spend'

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['S3_BUCKET'] = 'receipt-scan-sustainable-spend'
# Set AWS credentials using environment variables
os.environ['AWS_ACCESS_KEY_ID'] = 'ACCESS_KEY_ID'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'SECRET_ACCESS_KEY'
os.environ['AWS_DEFAULT_REGION'] = 'ap-south-1'

s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
dynamodb_client = boto3.client('dynamodb')


genai.configure(api_key="AIzaSyA5clcHzgVRUThbdp6rL9kKvtKhw9lOCyM")  # Replace with your actual API key
model = genai.GenerativeModel(model_name="gemini-1.0-pro", generation_config={
    "temperature": 0.9,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 2048,
}, safety_settings=[
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
])

def extract_text_from_document_s3(bucket_name, object_key):
    try:
        print("Extracting text from document in S3...")
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
        document_bytes = response['Body'].read()
        response = textract_client.detect_document_text(Document={'Bytes': document_bytes})
        extracted_text = process_text_response(response)
        return extracted_text
    except Exception as e:
        print(f"Error extracting text from document: {e}")
        return None

def process_text_response(response):
    try:
        print("Processing text response...")
        extracted_text = []
        for block in response['Blocks']:
            if block['BlockType'] == 'LINE':
                extracted_text.append(block['Text'])
        return ' '.join(extracted_text)
    except Exception as e:
        print(f"Error processing text response: {e}")
        return None

def get_most_recent_image_key(bucket_name, folder_name):
    try:
        print("Getting most recent image key...")
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=folder_name+'/')
        for obj in response.get('Contents', []):
            if obj['Key'].lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                return obj['Key']
    except Exception as e:
        print(f"Error getting most recent image key: {e}")
    return None

def store_text_in_dynamodb(text):
    try:
        print("Storing text in DynamoDB...")
        receipt_id = str(uuid.uuid4())
        
        print(session.get('receipt_id'))
        dynamodb_data = {
            'Receipt_ID': {'S': receipt_id},
            'Text': {'S': text}
        }
        response = dynamodb_client.put_item(
            TableName='Receipt_Items',
            Item=dynamodb_data
        )
        return response
    except Exception as e:
        print(f"Error storing text in DynamoDB: {e}")
        return None

def extract_text_and_store_in_dynamodb(bucket_name, folder_name):
    try:
        print("Extracting text and storing in DynamoDB...")
        object_key = get_most_recent_image_key(bucket_name, folder_name)
        if object_key:
            extracted_text = extract_text_from_document_s3(bucket_name, object_key)
            if extracted_text:
                response = store_text_in_dynamodb(extracted_text)
                if response:
                    print("Text extracted and stored in DynamoDB successfully.")
                    process_text_and_generate_model_response()
                else:
                    print("Failed to store text in DynamoDB.")
            else:
                print("Failed to extract text from the image.")
        else:
            print("No image found in the bucket.")
    except Exception as e:
        print(f"Error extracting text and storing in DynamoDB: {e}")

def process_text_and_generate_model_response():
    try:
        print("Processing text and generating model response...")
        response = dynamodb_client.scan(TableName='Receipt_Items', Limit=1)
        if 'Items' in response and response['Items']:
            recent_text = response['Items'][0]['Text']['S']
            if recent_text:
                classification_sentence = "Give a sustainability combined score for all the items given, ignore any unnecessary details,  calculate the sustainabily score  using the formula given- number of sustainable items/total number of items in the receipt, only print the final score in decimal format"
                text_with_classification = f"{recent_text}\n{classification_sentence}"
                convo = model.start_chat(history=[])
                convo.send_message(text_with_classification)
                model_response = convo.last.text
                value_id = str(uuid.uuid4())
                f = open("ids.txt", "w")
                f.write(value_id)
                f.close()
                try:
                    response = dynamodb_client.put_item(
                        TableName='Data',
                        Item={
                            'Value_id': {'S': value_id},
                            'Sustainability_Score': {'S': model_response}
                        }
                    )
                    print("Model response added to DynamoDB table successfully.")
                except Exception as e:
                    print(f"Error adding model response to DynamoDB table: {e}")
            else:
                print("No recent text found in DynamoDB.")
        else:
            print("No items found in DynamoDB table.")
    except Exception as e:
        print(f"Error processing text and generating model response: {e}")


@app.route('/')
def index():
    name = session.get("username")
    if name:
        return render_template("index.html")
    return login()

@app.route('/index')
def index1():
    name = session.get("username")
    if name:
        return index()
    return login()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
       
        data = request.get_json()

        username = data['username']
        password = data['password']
        
        response = dynamodb_client.get_item(
            TableName='Users',
            Key={
                'Username': {'S': username}
            }
        )
        items = response.get('Item')
        
        if items:
            stored_password = items.get('password', {}).get('S')
            if password == stored_password:
                session['username'] = username
                return jsonify({"message": "login success"}), 200
        return jsonify({"message": "Invalid username or password"}), 401
    return render_template("login.html")

@app.route('/check', methods=['POST'])
def check():
    if request.method == 'POST':
        data = request.get_json()

        username = data['username']
        password = data['password']
        
        response = dynamodb_client.get_item(
            TableName='Users',
            Key={
                'Username': {'S': username}
            }
        )
        items = response['Item']
        
        if items:
            stored_password = items['password']['S']
            if password == stored_password:
                session['username'] = username
                return jsonify({"message" : "login success"}) , 200
        
    return jsonify({"message" : "ERROR" }), 500



@app.route('/register')
def register():
    return render_template('register.html')


@app.route('/signup', methods=['post'])
def signup():
    if request.method == 'POST':
        Username = request.form['Username']
        email = request.form['email']
        password = request.form['password']
        
        table = dynamodb_client.Table('Users')
        
        table.put_item(
                Item={
        'Username': Username,
        'email': email,
        'password': password
            }
        )
        msg = "Registration Complete. Please Login to your account !"
    
        return render_template('login.html',msg = msg)
    return render_template('register.html')

@app.route('/upload', methods=['POST'])
def upload():
    name = session.get('username')
    if name is None:
        render_template('login.html', msg="Login First!")
    print("Received upload request...")
    
    if 'file' not in request.files:
        print("No file part found in request.")
        return redirect(url_for('index', message='No file part'))

    file = request.files['file']

    if file.filename == '':
        print("No selected file.")
        return redirect(url_for('index', message='No selected file'))

    if file:
        print("Starting file upload process...")
        upload_dir = app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)

        filename = secure_filename(file.filename)
        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        s3_key = f'uploads/{filename}'
        s3_client.upload_file(file_path, app.config['S3_BUCKET'], s3_key)

        os.remove(file_path)

        extract_text_and_store_in_dynamodb(app.config['S3_BUCKET'], app.config['UPLOAD_FOLDER'])

        return redirect(url_for('index', message='File successfully uploaded to S3 and processed'))
    return redirect(url_for('index', message='Unexpected error occurred'))

@app.route('/fetch_score', methods=['GET'])
def fetch_score():
    f = open("ids.txt", "r")
    receipt_id = f.read()
    print(f.read())
    if receipt_id is None:
        return jsonify({'error' : 'Upload First!'}), 500
    try:
        response = dynamodb_client.get_item(
            TableName='Data',
            Key={
                'Value_id': {'S': receipt_id}
            }
        )
        score = response['Item']['Sustainability_Score']['S']
        return jsonify({'score': score}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/get_latest_image')
def get_latest_image():
    try:
        # Create a boto3 client to interact with S3
        s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
        
        # List objects in the specified folder of the S3 bucket
        print("Listing objects in S3 bucket...")
        response = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix='uploads/')
        print("Response:", response)

        # Get the most recent object (image) based on its LastModified timestamp
        recent_object = max(response['Contents'], key=lambda x: x['LastModified'])
        print("Recent object:", recent_object)

        # Get the key (path) of the most recent object
        object_key = recent_object['Key']
        print("Object key:", object_key)

        # Download the object (image) from S3 to a temporary file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_file:
            print("Downloading object from S3...")
            s3.download_fileobj(S3_BUCKET_NAME, object_key, temp_file)
            temp_file_path = temp_file.name

        # Send the image file back to the frontend
        print("Sending image file to frontend...")
        return send_file(temp_file_path, mimetype='image/png')

    except Exception as e:
        print("Error:", e)
        return str(e)

if __name__ == '__main__':
    app.run(debug=True)