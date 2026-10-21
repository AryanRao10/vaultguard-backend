from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
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
    "https://vaultguard-aicte.netlify.app",
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
    # 1. Flatten the text to destroy weird PDF line breaks and hidden spacing
    text_flat = " ".join(raw_text.split())
    
    extracted_data = {
        "Bank Name": None,
        "FDR Number": None,
        "Amount": None,
        "Issue Date": None,
        "Maturity Date": None
    }
    
    # 2. Ultra-forgiving Regex Patterns
    # Matches "STATE BANK OF INDIA" or "NATIONAL MERCHANTS BANK"
    bank_match = re.search(r'((?:[A-Za-z]+\s+){1,3}BANK(?:\s+OF\s+[A-Za-z]+)?)', text_flat, re.IGNORECASE)
    if bank_match:
        extracted_data["Bank Name"] = bank_match.group(1).strip()
        
    # Matches Account Number followed by any 6+ digits/letters
    fdr_match = re.search(r'Account Number[^\dA-Z]*([A-Z0-9]{6,20})', text_flat, re.IGNORECASE)
    if fdr_match:
        extracted_data["FDR Number"] = fdr_match.group(1)
        
    # Matches Amount followed by numbers and commas
    amount_match = re.search(r'Amount[^\d]*([\d,]{4,})', text_flat, re.IGNORECASE)
    if amount_match:
        extracted_data["Amount"] = amount_match.group(1).strip(',')
        
    # Matches Issue Date (e.g., 20-Oct-2026 or 20/10/2026)
    issue_match = re.search(r'(?:Deposit|Issue) Date[^\d]*([0-9]{1,2}[-/\s][A-Za-z0-9]{2,10}[-/\s][0-9]{2,4})', text_flat, re.IGNORECASE)
    if issue_match:
        extracted_data["Issue Date"] = issue_match.group(1)
        
    # Matches Maturity Date 
    maturity_match = re.search(r'Maturity Date[^\d]*([0-9]{1,2}[-/\s][A-Za-z0-9]{2,10}[-/\s][0-9]{2,4})', text_flat, re.IGNORECASE)
    if maturity_match:
        extracted_data["Maturity Date"] = maturity_match.group(1)

    # 3. Calculate true confidence score
    found_count = sum(1 for value in extracted_data.values() if value is not None)
    confidence_score = int((found_count / 5) * 100)
    
    return extracted_data, confidence_score

@app.get("/")
def read_root():
    return {"status": "VaultGuard API is running securely."}

@app.post("/api/v1/upload-fdr")
async def upload_fdr(
    file: UploadFile = File(...),
    parent_fdr_id: Optional[str] = Form(None)
):
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
    # 5. Database Logging (Sends to Supabase automatically)
    try:
        supabase.table("fdr_records").insert({
            "document_hash": document_hash,
            "filename": file.filename,
            "confidence_score": real_confidence,
            "status": "Verified" if real_confidence == 100 else "Manual Review Needed",
            "parent_fdr_id": parent_fdr_id
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

# --- NEW: REFUND & STATUS UPDATE ENDPOINT ---
class StatusUpdate(BaseModel):
    status: str

@app.patch("/api/v1/fdr/{fdr_id}/status")
async def update_fdr_status(fdr_id: str, payload: StatusUpdate):
    try:
        response = supabase.table("fdr_records").update({"status": payload.status}).eq("id", fdr_id).execute()
        return {"status": "success", "message": "Status updated successfully", "data": response.data}
    except Exception as e:
        return {"status": "error", "error": str(e)}
