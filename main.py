from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import re
import os
import hashlib
import io
import pdfplumber
from dotenv import load_dotenv

# Load Environment Variables from .env file
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize FastAPI App
app = FastAPI(title="VaultGuard AICTE API")

origins = [
    "http://localhost:3000",
    "https://beamish-pavlova-8e52cd.netlify.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW: SMART REGEX EXTRACTION FUNCTION ---
def extract_fdr_fields(raw_text):
    extracted_data = {
        "Bank Name": None,
        "FDR Number": None,
        "Amount": None,
        "Issue Date": None,
        "Maturity Date": None
    }
    
    # Extract Bank Name (looks for any line containing "Bank")
    bank_match = re.search(r'^(.*bank.*)$', raw_text, re.IGNORECASE | re.MULTILINE)
    if bank_match:
        extracted_data["Bank Name"] = bank_match.group(1).strip()
        
    # Extract FDR Number (looks for alphanumeric string after "Account Number")
    fdr_match = re.search(r'Account Number:?\s*([A-Z0-9]+)', raw_text, re.IGNORECASE)
    if fdr_match:
        extracted_data["FDR Number"] = fdr_match.group(1)
        
    # Extract Amount (ignores currency symbols, grabs numbers and commas)
    amount_match = re.search(r'Amount:?\s*[^\d]*([\d,]+)', raw_text, re.IGNORECASE)
    if amount_match:
        extracted_data["Amount"] = amount_match.group(1)
        
    # Extract Issue/Deposit Date (captures format like 20-Oct-2026)
    issue_match = re.search(r'(?:Deposit|Issue) Date:?\s*([\w-]+)', raw_text, re.IGNORECASE)
    if issue_match:
        extracted_data["Issue Date"] = issue_match.group(1)
        
    # Extract Maturity Date
    maturity_match = re.search(r'Maturity Date:?\s*([\w-]+)', raw_text, re.IGNORECASE)
    if maturity_match:
        extracted_data["Maturity Date"] = maturity_match.group(1)

    # Calculate real confidence score based strictly on fields found
    found_count = sum(1 for value in extracted_data.values() if value is not None)
    confidence_score = int((found_count / 5) * 100)
    
    return extracted_data, confidence_score

@app.get("/")
def read_root():
    return {"status": "VaultGuard API is running securely."}

@app.post("/api/v1/upload-fdr")
async def upload_fdr(file: UploadFile = File(...)):
    # 1. Read file bytes
    file_bytes = await file.read()
    
    # 2. SHA-256 Tamper-Evident Fingerprinting
    document_hash = hashlib.sha256(file_bytes).hexdigest()
    
    raw_text = ""
    
    # 3. Native Digital PDF Extraction (pytesseract removed to prevent Render errors)
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    raw_text += page_text + "\n"
                    
    except Exception as e:
        return {"error": f"Document parsing failed: {str(e)}"}

    # 4. Smart Data Parsing
    structured_data, real_confidence = extract_fdr_fields(raw_text)

    # 5. Database Logging (Sends to Supabase automatically)
    try:
        # Assuming you have a table named 'fdr_records' or similar. 
        # Update the table name if yours is different.
        supabase.table("fdr_records").insert({
            "document_hash": document_hash,
            "filename": file.filename,
            "confidence_score": real_confidence,
            "status": "Verified" if real_confidence == 100 else "Manual Review Needed"
        }).execute()
    except Exception as e:
        # Fails silently so it doesn't crash the upload if Supabase is sleeping
        print(f"Supabase logging failed: {str(e)}")

    # 6. Final JSON Response exactly mapped to Next.js fields
    return {
        "status": "success",
        "filename": file.filename,
        "hash": document_hash,             # Maps to frontend result.hash
        "confidence_score": real_confidence, # Maps to frontend result.confidence_score
        "structured_data": structured_data,  # Maps to the new form inputs
        "raw_text": raw_text
    }
