
"""MacroSnap: meal estimates, workout comparisons and WhatsApp/Email summaries."""


# Configuration and API clients

import hashlib
import re
import smtplib  # NEW: PYTHON'S BUILT-IN LIBRARY FOR SENDING EMAILS
from email.message import EmailMessage  # NEW: USED TO CREATE EMAIL MESSAGES
from io import BytesIO

import streamlit as st
from google import genai
from google.genai import errors, types
from PIL import Image, ImageOps, UnidentifiedImageError
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client as TwilioClient

from prompts import (       # WE WILL IMPORT THE PROMPTS FROM THE PROMPTS.PY FILE
    PHOTO_REQUEST_PROMPT,
    SUMMARY_REQUEST_PROMPT,
    SYSTEM_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
)


st.set_page_config(page_title="MacroSnap", page_icon="🥗", layout="centered")


def read_setting(name, default=""):
    try:
        return str(st.secrets.get(name, default)).strip()
    except FileNotFoundError:
        return default


#GEMINI CLIENT WE CREATED IS USED AGAIN AND AGAIN. AND WE WANT TO CACHE IT SO IT DOESN'T HAVE TO BE RECREATED EACH TIME.
@st.cache_resource(scope="session", on_release=lambda client: client.close())
def get_gemini_client(api_key):
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))


@st.cache_resource(scope="session")
def get_twilio_client(account_sid, auth_token):
    return TwilioClient(
        account_sid, auth_token, http_client=TwilioHttpClient(timeout=30)
    )


api_key = read_setting("GEMINI_API_KEY")
MODEL_NAME = read_setting("GEMINI_MODEL", "gemini-3.5-flash")

if not api_key or api_key.startswith("replace-"):
    st.title("🥗💪 MacroSnap")
    st.info("Copy .streamlit/secrets.toml.example to .streamlit/secrets.toml, "
            "add your Gemini API key, and restart the app.")
    st.stop()


gemini_client = get_gemini_client(api_key)


def get_chat_config():
    instructions = SYSTEM_PROMPT
    body_weight = st.session_state.get("body_weight")

    if body_weight is not None:
        instructions += (
            f"\nAt onboarding the user reported a body weight of {body_weight:g} kg. "
            "Use later corrections in the conversation in preference to this value. "
            "Do not assume this weight applies to older workouts."
        )

    return types.GenerateContentConfig(system_instruction=instructions)


# Helper functions
# THIS FUNCTION WILL RENDER THE MESSAGE IN CHAT UI INTERFACE.
def render_message(message):
    with st.chat_message(message["role"]):
        # IF THE MESSAGE IS AN IMAGE, WE RENDER THE IMAGE. IF THE MESSAGE IS AN ERROR, WE RENDER THE ERROR. IF THE MESSAGE IS TEXT, WE RENDER THE TEXT.
        if message["kind"] == "image":
            st.image(message["content"])
        elif message["kind"] == "error":
            st.error(message["content"])
        else:
            st.write(message["content"])


# THIS FUNCTION ADDS A MESSAGE TO THE CHAT HISTORY. ROLE MEANS WHO I SENDING THE MESSAGE. KIND MEANS WHAT TYPE OF MESSAGE IT IS THAT IS TEXT,IMAGE AUDIO ETC. CONTENT MEANS THE ACTUAL MESSAGE.
def add_message(role, kind, content):
    message = {"role": role, "kind": kind, "content": content}

    #ROLE CAN BE USER WHICH IS US AND ASSISTANT WHICH IS THE BOT.
    # INITIALLY THE MESSAGES LIST IS EMPTY. SO WE APPEND THE NEW MESSAGE TO THE LIST.
    st.session_state.messages.append(message)
    render_message(message)

    #RENDER MESSAGE FUNCTION RENDERS THE MESSAGE IN THE CHAT INTERFACE.


def prepare_photo(raw_bytes):
    if not raw_bytes or len(raw_bytes) > 5 * 1024 * 1024:
        raise ValueError("Use a JPEG or PNG photo smaller than 5 MB.")

    with Image.open(BytesIO(raw_bytes)) as source:
        if source.format not in {"JPEG", "PNG"}:
            raise ValueError("Please upload a JPEG or PNG photo.")

        if source.width * source.height > 20_000_000:
            raise ValueError("Resize the photo to less than 20 megapixels.")

        picture = ImageOps.exif_transpose(source).convert("RGB")
        picture.thumbnail((1600, 1600))

        output = BytesIO()
        picture.save(output, format="JPEG", quality=90)

    return output.getvalue()


def ask_gemini(parts, summary_only=False):
    # A temporary chat means failed calls and summary prompts do not change
    # the main conversation. Save its updated history only after a good reply.
    try:
        candidate = gemini_client.chats.create(
            model=MODEL_NAME,
            config=get_chat_config(),
            history=st.session_state.chat.get_history(curated=True),
        )

        answer = (candidate.send_message(parts).text or "").strip()

        if not answer:
            return None, "Gemini returned no text. Try a clearer photo or description."

        if not summary_only:
            st.session_state.chat = candidate

        return answer, None

    except errors.APIError as error:
        if error.code == 429:
            return None, "Gemini quota or rate limit reached. Check your AI Studio quota."

        if error.code in {400, 401, 403}:
            return None, "Gemini rejected the request. Check your API key, model access, and input."

        if error.code == 404:
            return None, "Model unavailable. Set GEMINI_MODEL to a vision model available to your key."

        return None, "Gemini could not complete the request. Try again shortly."

    except Exception:
        return None, "Could not reach Gemini. Check your connection and try again."


# WHATSAPP FUNCTION - ORIGINAL TWILIO IMPLEMENTATION RETAINED
def send_whatsapp(to_number, body):  # HERE WE HAVE TO_NUMBER WHICH IS THE WHATSAPP NUMBER AND BODY WHICH IS THE SUMMARY.
    #WE WILL USE TWILIO TO SEND THE SUMMARY TO THE WHATSAPP NUMBER.
    sid = read_setting("TWILIO_ACCOUNT_SID")
    token = read_setting("TWILIO_AUTH_TOKEN")
    sender = read_setting("TWILIO_WHATSAPP_FROM")

    if not sid or not token or sid.startswith("replace-") or token.startswith("replace-"):
        return False, "Add your Twilio Account SID and Auth Token to secrets.toml first."

    if not re.fullmatch(r"whatsapp:\+[1-9][0-9]{7,14}", sender):
        return False, "Set TWILIO_WHATSAPP_FROM to the WhatsApp sender shown in your Twilio console."

    try:
        client = get_twilio_client(sid, token)
        message = client.messages.create(
            from_=sender, to=f"whatsapp:{to_number}", body=body
        )

        return True, f"Twilio accepted the message (status: {message.status}). " \
                     f"Delivery is not yet confirmed. Message SID: {message.sid}"

    except TwilioRestException as error:
        explanations = {
            20003: "Twilio authentication failed. Check your Account SID and Auth Token.",
            21608: "Verify this recipient in your Twilio trial setup.",
            63015: "Join or rejoin your Twilio Sandbox from this WhatsApp number.",
            63016: "The 24-hour window is closed. Send a WhatsApp message to your testing number, then retry.",
        }

        return False, explanations.get(
            error.code,
            f"Twilio rejected the message (code {error.code}). Check its Messaging logs."
        )

    except Exception:
        return False, "The send result is unknown. Check Twilio Messaging logs before retrying to avoid a duplicate."


# NEW: EMAIL FUNCTION USING GMAIL SMTP
# THIS FUNCTION SENDS THE SAME SUMMARY TO THE USER'S EMAIL ADDRESS INSTEAD OF WHATSAPP.
def send_email(to_address, subject, body):
    gmail_address = read_setting("GMAIL_ADDRESS")
    gmail_app_password = read_setting("GMAIL_APP_PASSWORD")

    # WE READ OUR EMAIL CREDENTIALS FROM SECRETS.TOML, NOT DIRECTLY FROM THE CODE.
    if not gmail_address or not gmail_app_password:
        return False, "Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to secrets.toml first."

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to_address):
        return False, "Please enter a valid recipient email address."

    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = gmail_address
        message["To"] = to_address
        message.set_content(body)

        # SMTP_SSL CONNECTS TO GMAIL SECURELY THROUGH PORT 465.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(gmail_address, gmail_app_password)
            server.send_message(message)

        return True, "Email accepted by Gmail for sending. Check your inbox."

    except smtplib.SMTPAuthenticationError:
        return False, "Gmail authentication failed. Check your Gmail address and App Password."

    except (smtplib.SMTPException, OSError):
        return False, "Email could not be sent. Check Gmail settings and your connection."


# Onboarding
if not st.session_state.get("onboarded", False):
    st.title("🥗💪 MacroSnap")
    st.caption("Track meals and workouts. Compare sessions. Send yourself a summary.")

    with st.form("onboarding_form"):
        name = st.text_input("Your name", max_chars=60)

        # NEW: USER CAN CHOOSE WHETHER TO RECEIVE THE SUMMARY THROUGH EMAIL OR WHATSAPP.
        send_method = st.radio(
            "Where should we send your summary?",
            ["Email", "WhatsApp"],
            horizontal=True,
        )

        # NEW: THE INPUT CHANGES ACCORDING TO THE SELECTED METHOD.
        if send_method == "Email":
            destination = st.text_input(
                "Your email address",
                placeholder="you@gmail.com",
            )
        else:
            destination = st.text_input(
                "Your WhatsApp number",
                placeholder="+91XXXXXXXXXX",
            )

        body_weight = st.number_input(
            "Body weight in kg (optional)",
            min_value=20.0, max_value=400.0, value=None, step=0.1,
            help="Used for rough exercise-calorie estimates. You can correct it in chat.",
        )

        submitted = st.form_submit_button("Start chatting")

    if submitted:
        if send_method == "WhatsApp":
            destination = re.sub(r"[\s()\-]", "", destination)
        else:
            destination = destination.strip()

        if not name.strip():
            st.warning("Please enter your name.")

        elif send_method == "WhatsApp" and not re.fullmatch(r"\+[1-9][0-9]{7,14}", destination):
            st.warning("Include the country code, for example +91 followed by 10 digits.")

        elif send_method == "Email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", destination):
            st.warning("Please enter a valid email address.")

        else:
            st.session_state.name = name.strip()

            # NEW: SAVE THE USER'S SELECTED COMMUNICATION METHOD AND DESTINATION.
            st.session_state.send_method = send_method
            st.session_state.destination = destination

            # ORIGINAL WHATSAPP SESSION VARIABLE IS ALSO RETAINED.
            st.session_state.whatsapp_number = destination if send_method == "WhatsApp" else ""
            st.session_state.email_address = destination if send_method == "Email" else ""

            st.session_state.body_weight = body_weight
            st.session_state.chat = gemini_client.chats.create(
                model=MODEL_NAME, config=get_chat_config()
            )

            st.session_state.messages = [{
                "role": "assistant", "kind": "text",
                "content": WELCOME_MESSAGE_TEMPLATE.format(name=name.strip()),
            }]

            st.session_state.successful_turns = 0
            st.session_state.summary_draft = ""
            st.session_state.sent_payloads = set()
            st.session_state.recent_whatsapp_message = False

            st.session_state.onboarded = True  #WHEN THE USER SUBMITS THE FORM, WE SET THE ONBOARDED STATE TO TRUE
            st.rerun()  #WE RE-RUN THE APP TO SHOW THE NEW STATE

    st.caption(
        "Your chat, photos and supplied body weight go to Gemini. "
        "Sending a summary shares it with your selected email or WhatsApp provider."
    )
    st.stop()


#AFTER THE ONBOARDING WE CREATE A CHAT INTERFACE
# THIS IS LIKE THE NEXT PAGE THAT COMES AFTER WE ENTER OUR NAME AND WHATSAPP NUMBER
# Chat interface — same title/button layout as the workshop

header_col, button_col = st.columns([5, 2], vertical_alignment="center")

with header_col:
    st.title("🥗💪 MacroSnap")


# NEW: DISPLAY THE SELECTED DELIVERY METHOD.
st.caption(
    f"Hi {st.session_state.name} · "
    f"{st.session_state.send_method}: {st.session_state.destination}"
)

st.caption(
    "Enter meals or exercises with weights, reps and sets. For calorie estimates, "
    "include body weight, duration, effort and rest details."
)

st.caption("History lasts for this browser session only. Calorie estimates are approximate.")

summary_panel = st.container()

for message in st.session_state.messages:
    render_message(message)


# Handle text and photo input
user_input = st.chat_input(
    "Enter a meal or workout, ask about progress, or attach a photo", #THIS IS THE PLACEHOLDER TEXT THAT WILL BE DISPLAYED IN THE CHAT INPUT BOX.
    accept_file=True, #THIS ALLOWS THE USER TO UPLOAD FILES.
    file_type=["jpg", "jpeg", "png"], #THIS ALLOWS THE USER TO UPLOAD ONLY JPG, JPEG AND PNG FILES. WE CAN ALSO ADD PDF, DOCX, XLSX, PPTX FILES IF WE WANT.
    max_upload_size=5, #THIS ALLOWS THE USER TO UPLOAD ONLY FILES UP TO 5 MB.
    max_chars=4000, #THIS ALLOWS THE USER TO ENTER ONLY 4000 CHARACTERS.
)

if user_input: #IF THE USER ENTERS TEXT OR UPLOADS A FILE, WE PROCESS THE INPUT.
    text = (user_input.text or "").strip() #IF THE USER INPUT IS ONLY TEXT, then only we store the text in the text variable.
    photo = user_input.files[0] if user_input.files else None #IF THE USER INPUT IS A FILE OR PHOTO, WE STORE THE FILE IN THE photo variable. OTHERWISE WE SET IT TO NONE.
    photo_bytes = None #INITIALLY WE SET THE PHOTO BYTES TO NONE.

    try:
        if photo is not None: #IF THE USER UPLOADS A PHOTO, WE PREPARE THE PHOTO BYTES.
            photo_bytes = prepare_photo(photo.getvalue()) #WE PREPARE THE PHOTO BYTES.

    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        st.warning(str(error) if isinstance(error, ValueError) else "That image could not be opened.") #IF THE PHOTO IS NOT A VALID IMAGE, WE SHOW A WARNING.
        st.stop()

    if text or photo_bytes:
        st.session_state.summary_draft = ""
        st.session_state.recent_whatsapp_message = False

        parts = []

        if photo_bytes:
            add_message("user", "image", photo_bytes)
            parts.append(types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg")) # WE CANT STORE IMAGES ENTIRELY IN THE MEMORY. SO WE STORE THE PART BY  PART PHOTO BYTES IN THE PARTS LIST.

        if text:
            add_message("user", "text", text) #FOR TEXT MESSAGES, WE ADD THE TEXT TO THE PARTS LIST.

        parts.append(text or PHOTO_REQUEST_PROMPT)

        with st.spinner("Reviewing your meal or workout..."):
            answer, error_message = ask_gemini(parts) #WE ASK GEMINI WITH THE IMAGES AND TEXT PARTS WE HAVE CREATED.

        if error_message:
            add_message("assistant", "error", error_message)
        else:
            add_message("assistant", "text", answer) #AS WE ADD  THE MESSAGE, RENDER_MESSAGE FUNCTION WILL RENDER THE MESSAGE IN THE CHAT INTERFACE.
            st.session_state.successful_turns += 1


# Prepare, review and send the combined summary
with button_col:
    prepare_summary = st.button(
        "Prepare summary",
        disabled=st.session_state.successful_turns == 0,
        width="stretch",
    )


with summary_panel:
    with st.expander(
        "📤 Review meal + workout summary",
        expanded=prepare_summary or bool(st.session_state.summary_draft),
    ):
        if prepare_summary:
            st.session_state.summary_draft = ""
            st.session_state.recent_whatsapp_message = False

            with st.spinner("Preparing your summary..."):
                summary, error_message = ask_gemini(
                    [SUMMARY_REQUEST_PROMPT],
                    summary_only=True,
                )

            if error_message:
                st.error(error_message)
            else:
                st.session_state.summary_draft = summary

        if st.session_state.summary_draft:
            draft = st.text_area(
                "Review or edit before sending",
                key="summary_draft",
                height=190,
            )

            body = f"Hi {st.session_state.name}! Your MacroSnap summary:\n\n{draft.strip()}"

            st.caption(
                f"To: {st.session_state.destination} · "
                f"{len(body)} characters including greeting"
            )

            st.download_button(
                "Download summary",
                body,
                "macrosnap-summary.txt",
                mime="text/plain",
            )

            # NEW: WHATSAPP HAS A 24-HOUR WINDOW REQUIREMENT.
            # EMAIL DOES NOT REQUIRE THE WHATSAPP SANDBOX OR THIS CHECKBOX.
            if st.session_state.send_method == "WhatsApp":
                st.info(
                    "For testing, join your Sandbox and send it a WhatsApp message now. "
                    "Free-form summaries need an open 24-hour reply window."
                )

                recent_message = st.checkbox(
                    "I sent a WhatsApp message to this testing number within the last 24 hours.",
                    key="recent_whatsapp_message",
                )

                too_long = len(body) > 1500

                if too_long:
                    st.warning(
                        "Shorten the summary to 1,500 characters including the greeting. "
                        "Nothing will be truncated."
                    )

            else:
                recent_message = True
                too_long = False

            digest = hashlib.sha256(
                (st.session_state.send_method + st.session_state.destination + body).encode()
            ).hexdigest()

            already_sent = digest in st.session_state.sent_payloads

            if already_sent:
                st.caption("This exact summary was already submitted during this session.")

            # NEW: SEND BUTTON LABEL AND FUNCTION DEPEND ON USER'S CHOICE.
            if st.button(
                f"Send this summary to {st.session_state.send_method}",
                disabled=not recent_message or not draft.strip() or too_long or already_sent,
            ):
                with st.spinner(f"Submitting to {st.session_state.send_method}..."):

                    if st.session_state.send_method == "Email":
                        success, info = send_email(
                            st.session_state.email_address,
                            "Your MacroSnap Meal + Workout Summary",
                            body,
                        )

                    else:
                        success, info = send_whatsapp(
                            st.session_state.whatsapp_number,
                            body,
                        ) #WHEN ITS SUCCEESS, WE SEND THE SUMMARY TO THE WHATSAPP NUMBER.

                if success:
                    st.session_state.sent_payloads.add(digest)
                    st.success(info)

                else:
                    st.error(info)
