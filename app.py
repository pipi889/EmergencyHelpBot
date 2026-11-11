from flask import Flask, request, abort
from dotenv import load_dotenv
import os
import requests
from math import radians, sin, cos, sqrt, atan2

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, LocationMessage

from content import CASES, SIMPLE_REPLIES, DEFAULT_REPLY, KEYWORD_ORDER
from flex_messages import build_flex, build_default_menu, build_call_1669

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

def distance_km(lat1, lon1, lat2, lon2):
    R = 6371

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c

@app.route("/")
def home():
    return "Emergency Help Bot Running"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    print("BODY =", body)
    print("SIGNATURE =", signature)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()

    print("ได้รับข้อความ:", user_text)

    matched_keyword = None
    for keyword in KEYWORD_ORDER:
        if keyword in user_text:
            matched_keyword = keyword
            break

    if matched_keyword is None:
        line_bot_api.reply_message(
            event.reply_token,
            build_default_menu()
        )
        return

    if matched_keyword in CASES:
        # เคสที่มีข้อมูล structured -> ส่งเป็น Flex Message
        flex_message = build_flex(matched_keyword, CASES[matched_keyword])
        line_bot_api.reply_message(
            event.reply_token,
            flex_message
        )
    elif matched_keyword in SIMPLE_REPLIES:
        if matched_keyword == "โทร 1669":
            line_bot_api.reply_message(
                event.reply_token,
                build_call_1669()
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=SIMPLE_REPLIES[matched_keyword])
            )
    else:
        # เผื่อไว้ ถ้า keyword อยู่ใน KEYWORD_ORDER แต่ไม่มีข้อมูลจริง
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=DEFAULT_REPLY)
        )


@handler.add(MessageEvent, message=LocationMessage)
def handle_location(event):
    print("LOCATION RECEIVED")

    latitude = event.message.latitude
    longitude = event.message.longitude

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

    params = {
        "location": f"{latitude},{longitude}",
        "radius": 8000,
        "type": "hospital",
        "keyword": "โรงพยาบาล",
        "key": GOOGLE_API_KEY
    }

    response = requests.get(url, params=params)
    data = response.json()
    print("API STATUS:", response.status_code)
print("GOOGLE STATUS:", data.get("status"))
print("ERROR:", data.get("error_message"))
print("RESULT:", len(data.get("results", [])))

    hospitals = data.get("results", [])

    filtered_hospitals = []

    for hospital in hospitals:
        name = hospital.get("name", "")

     if name and (
    "Animal" not in name
    and "Veterinary" not in name
    and "Pet" not in name
    and "Vet" not in name
):
    filtered_hospitals.append(hospital)
            filtered_hospitals.append(hospital)

    filtered_hospitals.sort(
    key=lambda h: distance_km(
        latitude,
        longitude,
        h["geometry"]["location"]["lat"],
        h["geometry"]["location"]["lng"]
    )
)

    hospitals = filtered_hospitals[:3]

    reply_text = "🏥 โรงพยาบาลใกล้คุณ\n\n"

    if hospitals:
        for i, hospital in enumerate(hospitals, start=1):
            name = hospital.get("name", "ไม่ทราบชื่อ")

            lat = hospital["geometry"]["location"]["lat"]
            lng = hospital["geometry"]["location"]["lng"]


            distance = distance_km(
                latitude,
                longitude,
                lat,
                lng
            )

            map_link = f"https://maps.google.com/?q={lat},{lng}"

            reply_text += (
                f"{i}. {name}\n"
                f"📍 ระยะโดยประมาณ {distance:.1f} กม.\n"
                f"{map_link}\n\n"
            )
    else:
        reply_text += "ไม่พบโรงพยาบาลใกล้เคียง\n"

    reply_text += "\n🚑 หากเป็นเหตุฉุกเฉิน โปรดโทร 1669"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
