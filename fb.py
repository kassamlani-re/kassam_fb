import os
import json
import threading
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests

# مكتبات الذكاء الاصطناعي، قاعدة البيانات، وحساب المسافات والتشفير
from google import genai
from google.genai import types
from supabase import create_client, Client
from geopy.distance import geodesic
from cryptography.fernet import Fernet

# 1. تحميل المتغيرات السرية وإعداد الاتصالات
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")

# جلب مفتاح التشفير السري الموحد المحمي في سيرفر Render لخدمة كل التجار بأمان
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("خطأ حرج: لم يتم العثور على ENCRYPTION_KEY في متغيرات البيئة بسيرفر Render!")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# ----------------------------------------------------
# وظائف الأمان والتشفير البرمجي الموحد لبيئة الـ SaaS
# ----------------------------------------------------
def encrypt_token(plain_token: str) -> str:
    """ تشفير توكن التاجر قبل إرساله وحفظه في Supabase """
    if not plain_token: return ""
    return cipher_suite.encrypt(plain_token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """ فك تشفير توكن التاجر عند سحبه من Supabase للاستخدام الفوري بالذاكرة """
    if not encrypted_token: return ""
    try:
        return cipher_suite.decrypt(encrypted_token.encode()).decode()
    except Exception as e:
        print("خطأ حرج في فك التشفير، قد يكون المفتاح السري غير متطابق:", e)
        return ""

# ----------------------------------------------------
# الوظائف المساعدة الديناميكية المعزولة لكل تاجر حياً
# ----------------------------------------------------
def save_to_chat_history_dynamic(merchant_id, sender: str, message_text: str):
    """ حفظ كل رسالة كسطر مستقل مربوط بالتاجر المكتشف تلقائياً """
    try:
        data = {
            "merchant_id": merchant_id, 
            "sender": sender, 
            "message_text": message_text, 
            "platform": "messenger"
        }
        supabase.table("chat_history").insert(data).execute()
    except Exception as e: 
        print("خطأ في حفظ سجل المحادثة:", e)

def get_chat_context(sender_id: str, merchant_id: str) -> list:
    """ جلب الذاكرة وتصفيها برمجياً حسب معرف التاجر لـ Gemini (آخر 6 رسائل سياق) """
    try:
        query = supabase.table("chat_history") \
            .select("sender, message_text") \
            .eq("merchant_id", merchant_id) \
            .eq("platform", "messenger") \
            .order("created_at", ascending=False) \
            .limit(6) \
            .execute()
        contents = []
        for msg in reversed(query.data):
            role = "user" if msg['sender'] != "bot" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(msg['message_text']))]))
        return contents
    except Exception as e: 
        print("خطأ في سحب الذاكرة السياقية:", e)
        return []
def calculate_delivery_cost_dynamic(merchant_data, customer_lat: float, customer_lng: float) -> str:
    """ حساب المسافة والتوصيل من إحداثيات محل التاجر المسترجع حياً بأمان تآملي """
    try:
        merchant_coords = (merchant_data['latitude'], merchant_data['longitude'])
        distance_km = geodesic(merchant_coords, (customer_lat, customer_lng)).kilometers
        total_cost = distance_km * float(merchant_data['delivery_fee_per_km'])
        return f"المسافة التقريبية للمحل هي {round(distance_km, 2)} كم، وتكلفة التوصيل الإجمالية لموقعك هي: {round(total_cost, 2)} دينار."
    except Exception as e: 
        return f"خطأ في حساب تكلفة التوصيل الجغرافي: {str(e)}"

def check_and_book_appointment_dynamic(merchant_id, customer_name: str, customer_phone: str, requested_time_str: str) -> str:
    """ حجز المواعيد مع تنظيف وتأمين صيغ الوقت نصياً لمنع التضارب وانهيار السيرفر """
    try:
        clean_time_str = requested_time_str.strip()
        correct_datetime = None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                correct_datetime = datetime.strptime(clean_time_str, fmt)
                break
            except ValueError:
                continue
                
        if not correct_datetime:
            return "عذراً، لم أستطع ضبط صيغة الوقت بشكل صحيح. يرجى كتابة التاريخ والوقت بوضوح مثل: 2026-08-05 14:00."

        check_query = supabase.table("appointments") \
            .select("*") \
            .eq("merchant_id", merchant_id) \
            .eq("appointment_time", correct_datetime.isoformat()) \
            .eq("status", "confirmed") \
            .execute()
            
        if len(check_query.data) > 0: 
            return "نعتذر منك بشدة، هذا الوقت محجوز مسبقاً. يرجى اختيار موعد آخر وسأقوم بتأكيده لك فوراً."
            
        booking_data = {
            "merchant_id": merchant_id, 
            "customer_phone": customer_phone, 
            "customer_name": customer_name, 
            "appointment_time": correct_datetime.isoformat(), 
            "status": "confirmed"
        }
        supabase.table("appointments").insert(booking_data).execute()
        return f"تم تأكيد حجزك بنجاح باسم {customer_name} في الوقت والتاريخ المحدد: {correct_datetime.strftime('%Y-%m-%d الساعة %H:%M')}."
    except Exception as e: 
        return f"خطأ أثناء معالجة حجز الموعد: {str(e)}"

# ----------------------------------------------------
# عقل البوت والتكامل مع Gemini والوظائف الذكية المحدثة للسلع
# ----------------------------------------------------
def get_merchant_products_live(merchant_id) -> list:
    """ سحب قائمة السلع الحية الخاصة بهذا التاجر بالذات من جدول products المشترك """
    try:
        query = supabase.table("products").select("*").eq("merchant_id", merchant_id).execute()
        return query.data if query.data else []
    except Exception as e:
        print("خطأ في سحب المنتجات من قاعدة البيانات:", e)
        return []

def process_message_with_gemini(sender_id, user_message, merchant_data, image_bytes_data=None):
    """ إرسال سياق نظيف ودعم قراءة الصور (Vision) ومطابقتها بالسلع الحية للتاجر """
    try:
        merchant_id = merchant_data.get('id')
        merchant_name = merchant_data.get('business_name', 'المحل')
        merchant_phone = merchant_data.get('phone_number', 'غير مسجل')
        business_type = merchant_data.get('business_type', 'خدماتي')
        provides_delivery = merchant_data.get('provides_delivery', False)

        # 1. سحب السلع والأسعار الحية للتاجر الحالي من جدول products الجديد
        live_products = get_merchant_products_live(merchant_id)
        products_context_str = json.dumps(live_products, ensure_ascii=False)

        # 2. جلب الذاكرة السياقية للرسائل
        history_contents = get_chat_context(sender_id, merchant_id)
        
        # 3. دعم معالجة الصور والنصوص معاً (Multimodal)
        user_parts = []
        if image_bytes_data:
            # إذا أرسل الزبون صورة إعلان، نمررها للبايتات الخاصة بـ Gemini ليفحصها حياً
            user_parts.append(types.Part.from_bytes(data=image_bytes_data, mime_type="image/jpeg"))
            user_parts.append(types.Part.from_text(text="افحص هذه الصورة وتعرف على السلعة الموجودة فيها وأعط الزبون سعرها من سياق المنتجات."))
        
        if user_message:
            user_parts.append(types.Part.from_text(text=str(user_message)))
            
        history_contents.append(types.Content(role="user", parts=user_parts))
        
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="check_and_book_appointment_dynamic",
                        description="تستخدم لحجز موعد جديد للزبون في العيادة أو المكتب بعد أخذ اسمه، هاتفه، والوقت المطلوب بصيغة YYYY-MM-DD HH:MM:SS.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_name": types.Schema(type=types.Type.STRING, description="اسم الزبون الكامل"),
                                "customer_phone": types.Schema(type=types.Type.STRING, description="رقم هاتف الزبون الحركي"),
                                "requested_time_str": types.Schema(type=types.Type.STRING, description="الوقت والتاريخ بصيغة YYYY-MM-DD HH:MM:SS")
                            },
                            required=["customer_name", "customer_phone", "requested_time_str"]
                        )
                    ),
                    types.FunctionDeclaration(
                        name="calculate_delivery_cost_dynamic",
                        description="تستخدم لحساب المسافة وتكلفة التوصيل الإجمالية للزبون بناءً على إحداثيات موقعه الجغرافي.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_lat": types.Schema(type=types.Type.NUMBER, description="خط العرض لموقع الزبون Latitude"),
                                "customer_lng": types.Schema(type=types.Type.NUMBER, description="خط الطول لموقع الزبون Longitude")
                            },
                            required=["customer_lat", "customer_lng"]
                        )
                    )
                ]
            )
        ]
        
        if provides_delivery:
            delivery_instruction = (
                "نحن نوفر خدمة التوصيل. إذا استفسر الزبون عن التوصيل، قل له: "
                "'نعم، التوصيل متوفر. يمكنك إضافة موقعك الدقيق من خلال الضغط على زر مشاركة الموقع لتصلنا إحداثياتك تلقائياً.'"
            )
        else:
            delivery_instruction = "نحن لا نوفر خدمة التوصيل نهائياً في هذا المحل، اعتذر للزبون بلطف إذا سألك عنها."
        
        system_instruction = (
            f"أنت المساعد الافتراضي الآلي لصفحة ({merchant_name}) المتخصصة في ({business_type}). "
            f"معلومات التواصل الحقيقية والوحيدة الخاصة بنا هي: رقم الهاتف الحركي هو ({merchant_phone}). "
            f"إليك قائمة المنتجات والسلع الحقيقية والحية المتوفرة في محلنا الآن بالأسعار والصور: ({products_context_str}). "
            "التزم بهذه القائمة حرفياً! إذا سألك الزبون عن سعر سلعة أو اسمها، أجب بناءً عليها فقط وممنوع اختراع أسعار. "
            "إذا طلب الزبون قائمة السلع أو المنيو، يجب أن تذكر في ردك جملة (إليك قائمة المنتجات الخاصة بنا:) ليقوم النظام بعرضها تلقائياً. "
            "إذا سأل الزبون عن منتج محدد نصاً أو عبر صورة أرسلها، أجب بالسعر من القائمة واكتب في نهاية رسالتك عبارة (إليك كارت السلعة المعنية:) ليقوم السيرفر بإرسال كارت السلعة المصور فوراً للزبون. "
            f"{delivery_instruction} "
            "أجب باختصار شديد وبأسلوب محترف، ولا تذكر جوجل أو منصة SaaS."
        )
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.1,
            max_output_tokens=250
        )
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=history_contents,
            config=config
        )
        
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "check_and_book_appointment_dynamic":
                    args = call.args
                    return check_and_book_appointment_dynamic(merchant_id, args['customer_name'], args['customer_phone'], args['requested_time_str'])
                elif call.name == "calculate_delivery_cost_dynamic":
                    if not provides_delivery: return "معذرةً، خدمة التوصيل غير متوفرة لدينا حالياً."
                    args = call.args
                    return calculate_delivery_cost_dynamic(merchant_data, args['customer_lat'], args['customer_lng'])
        
        return response.text if response.text else "مرحباً بك، كيف يمكنني مساعدتك اليوم؟"
        
    except Exception as e:
        print("خطأ في المعالجة عبر Gemini:", e)
        return "معذرةً، حدث خطأ مؤقت في نظام المعالجة الذكي."

# ----------------------------------------------------
# ميزات الـ Sprint الجديد: مؤشرات الكتابة والقوالب الرسومية الديناميكية للسلع من قاعدة البيانات
# ----------------------------------------------------

def send_facebook_action_dynamic(recipient_id, access_token, action_type="typing_on"):
    """ تشغيل أو إيقاف مؤشر الكتابة (typing_on / typing_off) بناءً على توكن التاجر المستهدف """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "sender_action": action_type
    }
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers, params=query_params)
    except Exception as e:
        print(f"خطأ في إرسال إشارة فيسبوك ({action_type}):", e)


def send_location_button_dynamic(recipient_id, access_token, text_message):
    """ إرسال زر تفاعلي رسمي (Quick Reply) يطلب من الزبون إرسال موقعه الجغرافي بنقرة واحدة """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": text_message,
            "quick_replies": [{"content_type": "location"}]
        }
    }
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers, params=query_params)
    except Exception as e:
        print("خطأ في إرسال زر مشاركة الموقع التفاعلي:", e)


def send_dynamic_products_carousel(recipient_id, access_token, merchant_id):
    """ سحب السلع حياً من جدول products وعرضها كمنيو متحرك بالصور والأسعار لتاجر الـ SaaS الحالي """
    try:
        # استعلام حي لجلب منتجات هذا التاجر بالتحديد من قاعدة البيانات
        query = supabase.table("products").select("*").eq("merchant_id", merchant_id).limit(10).execute()
        products_list = query.data if query.data else []
        
        if not products_list:
            return  # إذا لم يضف التاجر منتجات بعد، لا نرسل قالباً فارغاً
            
        elements = []
        for prod in products_list:
            elements.append({
                "title": str(prod.get('title', 'سلعة متوفرة')),
                "image_url": str(prod.get('image_url', '')),
                "subtitle": f"السعر: {prod.get('price', 0)} دينار | {prod.get('subtitle', '')}",
                "buttons": [
                    {
                        "type": "postback",
                        "title": "🛒 طلب السلعة",
                        "payload": f"BUY_PRODUCT_{prod.get('id')}"
                    }
                ]
            })
            
        url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements": elements
                    }
                }
            }
        }
        requests.post(url, json=payload, headers={"Content-Type": "application/json"}, params={"access_token": access_token})
        print(f"==> تم عرض المنيو الديناميكي حياً من قاعدة البيانات لتاجر الـ SaaS رقم: {merchant_id}")
    except Exception as e:
        print("خطأ في بناء أو إرسال المنيو الديناميكي المصور:", e)


def send_single_product_card(recipient_id, access_token, product_title, image_url, price, subtitle, product_id):
    """ إرسال كارت مصور لمنتج واحد فقط عندما يسأل الزبون عنه نصاً أو عبر إرسال صوره إعلان """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "generic",
                    "elements": [{
                        "title": str(product_title),
                        "image_url": str(image_url),
                        "subtitle": f"السعر الحالي: {price} دينار | {subtitle}",
                        "buttons": [{"type": "postback", "title": "🛒 اطلبها الآن", "payload": f"BUY_PRODUCT_{product_id}"}]
                    }]
                }
            }
        }
    }
    try:
        requests.post(url, json=payload, headers={"Content-Type": "application/json"}, params={"access_token": access_token})
    except Exception as e:
        print("خطأ في إرسال كارت السلعة المفردة:", e)


def send_messenger_reply_dynamic(recipient_id, text_reply, access_token):
    """ إرسال الرد النصي النهائي للزبون باستخدام توكن التاجر الصحيح ديناميكياً """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text_reply}
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, params=query_params)
        if response.status_code != 200:
            print(f"رفض الإرسال من طرف ميتا (كود {response.status_code}): {response.text}")
    except Exception as e:
        print("حدث خطأ أثناء محاولة الاتصال بخوادم ميتا الرسمية:", e)
# ----------------------------------------------------
# دالة معالجة الرسائل والذكاء الاصطناعي في الخلفية (Background Worker متعدد الوسائط)
# ----------------------------------------------------
def background_message_processor(messaging, merchant_data):
    """ معالجة الرسائل النصية والصور في الخلفية ودعم الذكاء الرسومي الحي للسلع """
    try:
        sender_id = messaging['sender']['id']
        merchant_id = merchant_data['id']
        
        # فك تشفير توكن التاجر حياً لحماية الـ SaaS
        encrypted_token = merchant_data.get('page_access_token')
        merchant_token = decrypt_token(encrypted_token) if encrypted_token else PAGE_ACCESS_TOKEN

        # تشغيل مؤشر الكتابة فوراً
        send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_on")

        image_bytes_data = None
        message_text = ""

        # 1. التقاط الصور والمرفقات (مثل صورة إعلان السلعة)
        if 'message' in messaging and 'attachments' in messaging['message']:
            for attachment in messaging['message']['attachments']:
                # أ) إذا كان المرفق موقع جغرافي تلقائي، نحسب التوصيل فوراً
                if attachment.get('type') == 'location':
                    coordinates = attachment['payload']['coordinates']
                    lat = coordinates['lat']
                    lng = coordinates['long']
                    
                    save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل موقعه الجغرافي]")
                    reply_text = calculate_delivery_cost_dynamic(merchant_data, lat, lng)
                    
                    save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                    send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
                    send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                    return
                
                # ب) إذا كان المرفق صورة، نقوم بتحميلها كبايتات ليفحصها Gemini حياً
                elif attachment.get('type') == 'image':
                    image_url = attachment['payload']['url']
                    try:
                        img_response = requests.get(image_url)
                        if img_response.status_code == 200:
                            image_bytes_data = img_response.content
                            save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل صورة إعلان/منتج]")
                    except Exception as img_err:
                        print("خطأ أثناء تحميل بايتات الصورة من فيسبوك:", img_err)

        # 2. التقاط النص العادي إذا وجد مصاحباً للصورة أو منفرداً
        if 'message' in messaging and 'text' in messaging['message']:
            message_text = messaging['message']['text']
            if not image_bytes_data:
                save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text=message_text)

        # 3. تمرير المعطيات (النص، وبايتات الصورة إن وجدت) إلى عقل Gemini متعدد الوسائط
        if message_text or image_bytes_data:
            reply_text = process_message_with_gemini(sender_id, message_text, merchant_data, image_bytes_data)
            
            save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
            
            # إغلاق مؤشر الكتابة قبل الإرسال للزبون
            send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
            
            # [السيناريو 1: طلب المنيو أو السلع بالكامل ديناميكياً]
            if "إليك قائمة المنتجات الخاصة بنا" in reply_text or "المنيو" in reply_text:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                send_dynamic_products_carousel(recipient_id=sender_id, access_token=merchant_token, merchant_id=merchant_id)
            
            # [السيناريو 2: استفسار عن سلعة محددة نصاً أو عبر صورة إعلان]
            elif "إليك كارت السلعة المعنية" in reply_text:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                
                # استخراج السلعة الحية المطابقة من جدول المنتجات لإرسال كارتها المصور فوراً
                # سنقوم بالبحث برمجياً في سلع التاجر الحالية لمطابقة ما وجده Gemini
                live_prods = supabase.table("products").select("*").eq("merchant_id", merchant_id).execute().data
                if live_prods:
                    for p in live_prods:
                        # إذا ذكر الموديل اسم المنتج في الرد، نرسل كارت هذا المنتج بالتحديد
                        if str(p.get('title')) in reply_text or (message_text and str(p.get('title')) in message_text):
                            send_single_product_card(
                                recipient_id=sender_id,
                                access_token=merchant_token,
                                product_title=p.get('title'),
                                image_url=p.get('image_url'),
                                price=p.get('price'),
                                subtitle=p.get('subtitle'),
                                product_id=p.get('id')
                            )
                            break
            
            # [السيناريو 3: طلب التوصيل الجغرافي]
            elif "زر مشاركة الموقع" in reply_text or "لتصلنا إحداثياتك تلقائياً" in reply_text:
                send_location_button_dynamic(sender_id, merchant_token, reply_text)
            
            # [السيناريو 4: رد نصي اعتيادي]
            else:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                
    except Exception as e:
        print("خطأ في معالجة الخلفية متعددة الوسائط:", e)


# ----------------------------------------------------
# مسارات استقبال وتحقق ويب هوك ميتا الموحد المحدثة الصارمة
# ----------------------------------------------------
@app.route('/', methods=['GET'])
def home():
    return "مرحباً بك! سيرفر منصة SaaS يعمل بنجاح ومستيقظ الآن 🚀", 200


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """ خطوة التحقق الآمنة مع ميتا عند ربط السيرفر لأول مرة """
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
    """ استقبال الرسالة وإعطاء فيسبوك رد فوري لمنع التكرار البنائي، ثم معالجة البيانات في الخلفية """
    data = request.get_json()
    if not data:
        return "بيانات فارغة", 400
        
    if data.get('object') in ['page', 'instagram']:
        try:
            entry_list = data['entry']
            for entry in entry_list:
                if 'messaging' in entry:
                    for messaging_event in entry['messaging']:
                        facebook_page_id = messaging_event['recipient']['id']
                        
                        merchant_query = supabase.table("merchants").select("*").execute()
                        merchant_data = None
                        
                        if merchant_query.data:
                            for merchant in merchant_query.data:
                                db_page_id = str(merchant.get('facebook_page_id', '')).strip()
                                if db_page_id == str(facebook_page_id).strip():
                                    merchant_data = merchant
                                    break
                                    
                        if not merchant_data:
                            continue
                        
                        # تشغيل دالة المعالجة في خيط مستقل (Thread) وإعادة رد 200 OK لفيسبوك فوراً لمنع التكرار
                        threading.Thread(target=background_message_processor, args=(messaging_event, merchant_data)).start()
                        
            return "OK", 200  # إشعار نجاح فوري لفيسبوك يمنعه من إعادة المحاولة والتكرار
        except Exception as e:
            print("خطأ في استقبال الويب هوك الرئيسي:", e)
            
    return "تم الاستلام", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
