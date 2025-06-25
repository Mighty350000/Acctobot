from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import pandas as pd
import openai
import os
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

app = Flask(__name__)
CORS(app)

# Configure OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")

# Configure MySQL connection
db = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASS"),
    database=os.getenv("DB_NAME")
)

@app.route("/preview", methods=["POST"])
def preview():
    file = request.files["bankfile"]
    df = pd.read_excel(file)

    required_cols = {"Date", "Narration", "Withdrawal", "Deposit"}
    if not required_cols.issubset(df.columns):
        return jsonify({"error": "Excel must contain Date, Narration, Withdrawal, Deposit columns"}), 400

    cursor = db.cursor()

    preview_data = []
    for _, row in df.iterrows():
        try:
            date = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
            narration = str(row["Narration"]).strip()
            amount = row["Withdrawal"] if not pd.isna(row["Withdrawal"]) else -row["Deposit"]
            vtype = "Payment" if amount > 0 else "Receipt"
            amount = abs(amount)

            cursor.execute("SELECT ledger FROM ledger_map WHERE narration = %s", (narration,))
            result = cursor.fetchone()
            if result:
                ledger = result[0]
            else:
                prompt = f"Suggest an appropriate accounting ledger name for this bank narration:\n\n\"{narration}\"\n\nOnly give the ledger name."
                response = openai.Completion.create(
                    engine="text-davinci-003",
                    prompt=prompt,
                    max_tokens=20,
                    temperature=0
                )
                ledger = response.choices[0].text.strip()
                cursor.execute("INSERT INTO ledger_map (narration, ledger) VALUES (%s, %s)", (narration, ledger))
                db.commit()

            preview_data.append({
                "date": date,
                "narration": narration,
                "amount": amount,
                "vtype": vtype,
                "ledger": ledger
            })
        except:
            continue

    return jsonify(preview_data)

@app.route("/generate-xml", methods=["POST"])
def generate_xml():
    vouchers = request.json.get("vouchers", [])
    bank_ledger = request.json.get("bankLedger", "Bank A/C")

    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "TALLYREQUEST").text = "Import Data"

    body = SubElement(envelope, "BODY")
    importdata = SubElement(body, "IMPORTDATA")
    reqdesc = SubElement(importdata, "REQUESTDESC")
    SubElement(reqdesc, "REPORTNAME").text = "Vouchers"
    reqdata = SubElement(importdata, "REQUESTDATA")

    for v in vouchers:
        voucher = SubElement(SubElement(reqdata, "TALLYMESSAGE"), "VOUCHER", VCHTYPE=v["vtype"], ACTION="Create")
        SubElement(voucher, "DATE").text = v["date"].replace("-", "")
        SubElement(voucher, "VOUCHERTYPENAME").text = v["vtype"]
        SubElement(voucher, "NARRATION").text = v["narration"]
        SubElement(voucher, "PARTYLEDGERNAME").text = bank_ledger

        entry1 = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        SubElement(entry1, "LEDGERNAME").text = v["ledger"]
        SubElement(entry1, "ISDEEMEDPOSITIVE").text = "Yes" if v["vtype"] == "Payment" else "No"
        SubElement(entry1, "AMOUNT").text = f"{-v['amount']:.2f}" if v["vtype"] == "Payment" else f"{v['amount']:.2f}"

        entry2 = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        SubElement(entry2, "LEDGERNAME").text = bank_ledger
        SubElement(entry2, "ISDEEMEDPOSITIVE").text = "No" if v["vtype"] == "Payment" else "Yes"
        SubElement(entry2, "AMOUNT").text = f"{v['amount']:.2f}" if v["vtype"] == "Payment" else f"{-v['amount']:.2f}"

    xml_data = parseString(tostring(envelope)).toprettyxml(indent="    ")
    return jsonify({"xml": xml_data})

if __name__ == "__main__":
    app.run()
