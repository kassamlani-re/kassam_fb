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
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")

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
@app.route('/', methods=['GET'])
def home():
    return "مرحباً بك! سيرفر منصة SaaS يعمل بنجاح ومستيقظ الآن 🚀", 200

@app.route('/webhook', methods=['GET'])
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

# جلب مفتاح الصفحة الطويل من إعدادات السيرفر السحابي

@app.route('/webhook', methods=['POST'])
def handle_messages():
    """ استقبال رسائل الزبائن الفورية والرد عليها تلقائياً """
    data = request.get_json()
    
    if not data:
        return "بيانات فارغة", 400
        
    if data.get('object') in ['page', 'instagram']:
        try:
            # قراءة الرسالة ونصها بأمان من فيسبوك
            entry = data['entry'][0]
            if 'messaging' in entry:
                messaging = entry['messaging'][0]
                
                if 'message' in messaging and 'text' in messaging['message']:
                    sender_id = messaging['sender']['id'] # معرف حساب الزبون
                    message_text = messaging['message']['text'] # نص رسالة الزبون
                    
                    print(f"[ماسنجر] رسالة من {sender_id}: {message_text}")
                    
                    # نص الرد العكسي التلقائي للتجربة
                    reply_text = f"مرحباً! لقد استلمت رسالتك: '{message_text}' وجاري معالجتها بالذكاء الاصطناعي.. 🤖"
                    
                    # تشغيل دالة الإرسال لرد الرسالة لهاتف الزبون
                    send_messenger_reply(sender_id, reply_text)
                    
        except Exception as e:
            print("خطأ في قراءة رسالة ماسنجر:", e)
        
    return "تم الاستلام", 200

def send_messenger_reply(recipient_id, text_reply):
    """ دالة ترسل الرد المكتوب إلى حساب الزبون في ماسنجر عبر المفتاح الطويل """
    # الرابط الصحيح والمعدل لإرسال طلبات محادثات ماسنجر الرسمية من ميتا
    url = f"https://facebook.com{PAGE_ACCESS_TOKEN}"
    
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text_reply}
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            print("==> تم إرسال الرد العكسي للزبون بنجاح!")
        else:
            print(f"فشل إرسال الرد. خطأ ميتا: {response.text}")
    except Exception as e:
        print("حدث خطأ أثناء محاولة إرسال الطلب لميتا:", e)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
