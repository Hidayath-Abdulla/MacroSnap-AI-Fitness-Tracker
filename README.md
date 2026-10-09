# 🥗💪 MacroSnap — AI Nutrition & Fitness Assistant

MacroSnap is an AI-powered nutrition and workout assistant built using Google Gemini and Streamlit.

## Features

- 📸 **Meal Analysis:** Upload food images to estimate calories and macronutrients.
- 💪 **Workout Tracking:** Record exercises, sets, reps and weights.
- 📈 **Progress Comparison:** Compare workout performance within the current conversation.
- 🔥 **Exercise Calorie Estimation:** Estimate calories burned using approximate MET-based calculations.
- 🤖 **AI Chat:** Ask nutrition and fitness questions with Gemini.
- 📧 **Email Summaries:** Receive meal and workout summaries via Gmail.
- 📱 **WhatsApp Integration:** Optional messaging through Twilio.

## Technologies Used

- Python
- Streamlit
- Google Gemini API
- Gmail SMTP
- Twilio WhatsApp API
- Pillow

## Installation

1. Clone this repository.
2. Install dependencies:

   `pip install -r requirements.txt`

3. Create `.streamlit/secrets.toml` using `secrets.toml.example` as a reference.
4. Add your Gemini API key and email credentials.
5. Run:

   `streamlit run app.py`

## Limitations

- Nutritional and exercise calorie estimates are approximate.
- Workout history is maintained within the current application session only.
- WhatsApp messaging requires valid Twilio configuration and compliance with WhatsApp messaging rules.

## Security

API keys and passwords are not stored in the public repository.

##Demo : https://macrosnap-ai-fitness-tracker.streamlit.app/
