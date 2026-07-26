import os
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests

# مكتبات الذكاء الاصطناعي، قاعدة البيانات، وحساب المسافات
from google import genai
from google.genai import types
from supabase import create_client, Client
from geopy.distance import geodesic

# 1. تحميل المتغيرات السرية وإعداد الاتصالات
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# ----------------------------------------------------
# الوظيفة الأولى: دالة فحص وحجز المواعيد
# ----------------------------------------------------
def check_and_book_appointment(merchant_id, customer_phone, customer_name, requested_time_str):
    try:
        requested_time = datetime.strptime(requested_time_str, '%Y-%m-%d %H:%M:%S')
        
        check_query = supabase.table("appointments") \
            .select("*") \
            .eq("merchant_id", merchant_id) \
            .eq("appointment_time", requested_time.isoformat()) \
            .eq("status", "confirmed") \
            .execute()
            
        if len(check_query.data) > 0:
            return {"status": "busy", "message": "هذا الوقت محجوز مسبقاً."}
        else:
            booking_data = {
                "merchant_id": merchant_id,
                "customer_phone": customer_phone,
                "customer_name": customer_name,
                "appointment_time": requested_time.isoformat(),
                "status": "confirmed"
            }
            supabase.table("appointments").insert(booking_data).execute()
            return {"status": "success", "message": "تم تأكيد الحجز بنجاح."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ----------------------------------------------------
# الوظيفة الثانية: دالة حساب تكلفة التوصيل بناءً على الموقع
# ----------------------------------------------------
def calculate_delivery_cost(merchant_id, customer_lat, customer_lng):
    try:
        # جلب إحداثيات محل التاجر وسعر التوصيل من قاعدة البيانات
        merchant_query = supabase.table("merchants").select("latitude", "longitude", "delivery_fee_per_km").eq("id", merchant_id).single().execute()
        merchant_data = merchant_query.data
        
        if not merchant_data:
            return {"status": "error", "message": "لم يتم العثور على بيانات التاجر."}
            
        merchant_coords = (merchant_data['latitude'], merchant_data['longitude'])
        customer_coords = (customer_lat, customer_lng)
        
        # حساب المسافة بالكيلومترات (مسافة خط مستقيم دقيقة رياضياتياً)
        distance_km = geodesic(merchant_coords, customer_coords).kilometers
        
        # حساب التكلفة الكلية
        total_cost = distance_km * float(merchant_data['delivery_fee_per_km'])
        
        return {
            "status": "success",
            "distance_km": round(distance_km, 2),
            "delivery_cost": round(total_cost, 2)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ----------------------------------------------------
# الوظيفة الثالثة: الـ Webhook (مستقبل ومحقق رسائل ميتا الموحد)
# ----------------------------------------------------
@app.route('/webhook', methods=['GET'])
@app.route('/', methods=['GET'])
def home():
    return "مرحباً بك! سيرفر منصة SaaS يعمل بنجاح ومستيقظ الآن 🚀", 200

def verify_webhook():
    """ خطوة التحقق التي تطلبها ميتا عند ربط السيرفر """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode and token:
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("==> تم التحقق من السيرفر بنجاح من قبل ميتا!")
            return challenge, 200
        else:
            return "توكن غير متطابق", 403
    return "طلب غير صالح", 400

@app.route('/webhook', methods=['POST'])
def handle_messages():
    """ استقبال الرسائل الفورية من واتساب، ماسنجر، وإنستغرام """
    data = request.get_json()
    
    # فرز وتوجيه الرسائل بناءً على نوع المنصة
    if data.get('object') == 'whatsapp_business_account':
        # معالجة رسائل واتساب (سنربطها بـ Gemini لاحقاً)
        print("[واتساب] تم استقبال رسالة جديدة.")
    elif data.get('object') in ['page', 'instagram']:
        # معالجة رسائل ماسنجر وإنستغرام
        print(f"[{data.get('object')}] تم استقبال رسالة جديدة.")
        
    return "تم الاستلام", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
