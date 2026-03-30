from flask import Flask, request, render_template, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
import json
import random
from datetime import datetime
import os

app = Flask(__name__)

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
   api_key=os.getenv("GROQ_API_KEY") #connected to the .env file for security
)

chat_history = {}
completed_leads = set()

def extract_lead_info(chat_list):
    chat_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_list if msg['role'] != 'system'])
    extraction_prompt = f"""Read this conversation and extract:
    {{"name": "...", "pickup": "...", "dropoff": "...", "bedrooms": "...", "quote": "...", "notes": "..."}}
    Notes should include if they asked for a human or had specific furniture concerns.
    Conversation: {chat_text}"""
    
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": extraction_prompt}],
            response_format={"type": "json_object"} 
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"name": "Unknown", "pickup": "Unknown", "dropoff": "Unknown", "bedrooms": "Unknown", "quote": "N/A", "notes": "None"}

def create_ticket(phone_number, ticket_type, summary, extracted_data):
    ticket_id = f"TKT-{random.randint(1000, 9999)}"
    new_ticket = {
        "ticket_id": ticket_id,
        "phone": phone_number,
        "type": ticket_type, 
        "summary": summary,
        "time_created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN", 
        "lead_data": extracted_data, 
        "resolved_by": None,
        "time_resolved": None
    }
    tickets = []
    if os.path.exists("tickets.json"):
        with open("tickets.json", "r") as file:
            try: tickets = json.load(file)
            except: tickets = []
    tickets.append(new_ticket)
    with open("tickets.json", "w") as file:
        json.dump(tickets, file, indent=4)
    return ticket_id

system_prompt = """ROLE: Professional dispatcher for a moving company in Columbia, Maryland. 
OBJECTIVE: Collect 4 pieces of info: Name, Pickup, Dropoff, Bedrooms. 

STRICT RULES:
1. ONLY discuss moving. 
2. If the user asks for a human, is angry, or asks something you can't answer, you MUST reply with exactly: TRIGGER_HUMAN
3. Do NOT give the Setmore link until you have all 4 pieces of info.

PRICING: $200 base + $150/bedroom + $400 if outside MD.
Link: https://go.setmore.com/calendar"""

@app.route("/sms", methods=['POST'])
def sms_reply():
    sender = request.values.get('From')
    incoming_msg = request.values.get('Body', '')
    
    if sender not in chat_history:
        chat_history[sender] = [{"role": "system", "content": system_prompt}]
        welcome_msg = "👋 Hi! I'm your moving assistant. Who do I have the pleasure of speaking with?"
        chat_history[sender].append({"role": "assistant", "content": welcome_msg})
        resp = MessagingResponse()
        resp.message(welcome_msg)
        return str(resp)

    chat_history[sender].append({"role": "user", "content": incoming_msg})
    
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant", 
        messages=chat_history[sender]
    )
    answer = response.choices[0].message.content

    # CHECK FOR HUMAN HANDOFF OR COMPLETED LEAD
    if ("TRIGGER_HUMAN" in answer or "setmore.com" in answer.lower()) and sender not in completed_leads:
        ticket_type = "HUMAN_SUPPORT" if "TRIGGER_HUMAN" in answer else "NEW_LEAD"
        summary = "Customer requested a manager" if ticket_type == "HUMAN_SUPPORT" else "Quote Completed"
        
        extracted_info = extract_lead_info(chat_history[sender])
        create_ticket(sender, ticket_type, summary, extracted_info)
        completed_leads.add(sender)
        
        if "TRIGGER_HUMAN" in answer:
            answer = "I've flagged this for my manager. They will text you at this number shortly to assist you! 👋"

    chat_history[sender].append({"role": "assistant", "content": answer})
    resp = MessagingResponse()
    resp.message(answer)
    return str(resp)

@app.route("/dashboard")
def view_dashboard():
    tickets = []
    if os.path.exists("tickets.json"):
        with open("tickets.json", "r") as file:
            try: tickets = json.load(file)
            except: tickets = []
    open_tickets = [t for t in tickets if t.get('status', 'OPEN') != 'RESOLVED']
    resolved_tickets = [t for t in tickets if t.get('status') == 'RESOLVED']
    return render_template("dashboard.html", open_tickets=open_tickets, resolved_tickets=resolved_tickets)

@app.route("/update_ticket/<ticket_id>", methods=['POST'])
def update_ticket(ticket_id):
    data = request.json
    if os.path.exists("tickets.json"):
        with open("tickets.json", "r") as file:
            tickets = json.load(file)
        for t in tickets:
            if t["ticket_id"] == ticket_id:
                if data.get("status"): t["status"] = data.get("status")
                if data.get("admin_notes") is not None: t["lead_data"]["admin_notes"] = data.get("admin_notes")
                if data.get("status") == "RESOLVED":
                    t["time_resolved"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["resolved_by"] = data.get("resolver_name", "Admin")
                break
        with open("tickets.json", "w") as file:
            json.dump(tickets, file, indent=4)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(port=8080)