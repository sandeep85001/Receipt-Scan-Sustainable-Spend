from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
from werkzeug.utils import secure_filename
import boto3
import uuid
from datetime import datetime
import google.generativeai as genai

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['S3_BUCKET'] = 'receipt-scan-sustainable-spend'

# Set AWS credentials using environment variables
os.environ['AWS_ACCESS_KEY_ID'] = 'YOUR_ACCESS_KEY_ID'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'YOUR_SECRET_ACCESS_KEY'
os.environ['AWS_DEFAULT_REGION'] = 'YOUR_DEFAULT_REGION'

s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
dynamodb_client = boto3.client('dynamodb')

genai.configure(api_key="YOUR_GOOGLE_API_KEY")  # Replace with your actual API key
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
        objects = response.get('Contents', [])
        if objects:
            # Sort the objects based on the last modified timestamp in descending order
            sorted_objects = sorted(objects, key=lambda x: x['LastModified'], reverse=True)
            # Return the key of the most recent object
            return sorted_objects[0]['Key']
        else:
            print("No objects found in the specified folder.")
            return None
    except Exception as e:
        print(f"Error getting most recent image key: {e}")
        return None


def get_most_recent_receipt_id():
    try:
        response = dynamodb_client.scan(
            TableName='Receipt_Items',
            Limit=1,
            ScanIndexForward=False,  # Sort in descending order
            KeyConditionExpression='Receipt_ID > :start',
            ExpressionAttributeValues={
                ':start': {'S': str(datetime.utcnow().timestamp())}
            }
        )
        if 'Items' in response and response['Items']:
            return response['Items'][0]['Receipt_ID']['S']
    except Exception as e:
        print(f"Error getting most recent receipt ID: {e}")
    return None

def store_text_in_dynamodb(text):
    try:
        print("Storing text in DynamoDB...")
        receipt_id = str(uuid.uuid4())
        dynamodb_data = {
            'Receipt_ID': {'S': receipt_id},
            'Text': {'S': text}
        }
        response = dynamodb_client.put_item(
            TableName='Receipt_Items',
            Item=dynamodb_data
        )
        return receipt_id  # Return the generated receipt ID
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
                receipt_id = store_text_in_dynamodb(extracted_text)
                if receipt_id:
                    print("Text extracted and stored in DynamoDB successfully.")
                    process_text_and_generate_model_response(receipt_id)  # Pass the generated receipt ID
                else:
                    print("Failed to store text in DynamoDB.")
            else:
                print("Failed to extract text from the image.")
        else:
            print("No image found in the bucket.")
    except Exception as e:
        print(f"Error extracting text and storing in DynamoDB: {e}")

def process_text_and_generate_model_response(receipt_id):
    try:
        print("Processing text and generating model response...")
        # Use the provided receipt_id
        response = dynamodb_client.get_item(
            TableName='Receipt_Items',
            Key={'Receipt_ID': {'S': receipt_id}}
        )
        if 'Item' in response:
            recent_text = response['Item']['Text']['S']
            if recent_text:
                # Your model processing logic here
                print("Model response added to DynamoDB table successfully.")
            else:
                print("No recent text found in DynamoDB.")
        else:
            print("Receipt not found in DynamoDB.")
    except Exception as e:
        print(f"Error processing text and generating model response: {e}")

@app.route('/')
def index():
    upload_message = request.args.get('message')
    return render_template('index.html', upload_message=upload_message)

@app.route('/upload', methods=['POST'])
def upload():
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
    try:
        receipt_id = get_most_recent_receipt_id()
        if receipt_id:
            response = dynamodb_client.get_item(
                TableName='Data',
                Key={'Value_id': {'S': receipt_id}}
            )
            score = response['Item']['Sustainability_Score']
            return jsonify({'score': score})
        else:
            return jsonify({'error': 'No receipt found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
