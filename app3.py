import streamlit as st
from langchain_core.prompts import PromptTemplate
from langchain.memory import ConversationBufferMemory
import google.generativeai as genai
import requests
import re
import json
import random
from datetime import datetime, timedelta
import os

# Configure APIs
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAKYr5PF47Hbcx5LeujCzSXZH4ZrPpR9GU")  # Replace with valid key
genai.configure(api_key=GEMINI_API_KEY)

# Initialize Gemini model
model = genai.GenerativeModel("gemini-1.5-flash")

# Fixed exchange rate (1 USD = 300 LKR)
EXCHANGE_RATE = 300

# City data with coordinates
SRI_LANKA_CITIES = {
    "Colombo": {"lat": 6.9271, "lng": 79.8612},
    "Kandy": {"lat": 7.2906, "lng": 80.6337},
    "Galle": {"lat": 6.0535, "lng": 80.2210},
    "Ella": {"lat": 6.8667, "lng": 81.0466},
    "Mirissa": {"lat": 5.9483, "lng": 80.4589},
    "Sigiriya": {"lat": 7.9570, "lng": 80.7603}
}

# Prompt template for itinerary
TRAVEL_PLAN_PROMPT = PromptTemplate(
    input_variables=["user_input", "budget", "duration", "interests", "history", "starting_location", "end_location", "travel_style"],
    template="""
You are a travel planner for Sri Lanka. Create a {duration}-day itinerary from {starting_location} to {end_location} for {travel_style} travel.
- Format: **Day X: City** with activities, accommodation, transport, and costs in LKR (within {budget} LKR).
- Tailor to interests: {interests}.
- Use history: {history}.
- User input: {user_input}.
- End with budget summary.
Daily costs format:
**Accommodation:** [Price] LKR
**Food:** [Price] LKR
**Transport:** [Price] LKR
**Activities:** [Price] LKR
**Total:** [Price] LKR
"""
)

# Initialize memory
memory = ConversationBufferMemory(memory_key="history", input_key="user_input")

# Gemini API interaction
def get_gemini_response(prompt: str) -> str:
    try:
        response = model.generate_content(prompt)
        return response.text or "Error: Empty response."
    except Exception as e:
        return f"Error: {str(e)}"

# Fetch route details using OSRM
def get_route_details(start: str, destinations: list, end: str) -> dict:
    try:
        base_url = "http://router.project-osrm.org/route/v1/driving/"
        routes = []
        waypoints = [start] + destinations + [end]
        seen = set()

        for i in range(len(waypoints) - 1):
            origin, destination = waypoints[i], waypoints[i + 1]
            if origin.lower() == destination.lower() or destination.lower() in seen:
                continue
            seen.add(origin.lower())

            # Get coordinates for origin and destination
            origin_coords = SRI_LANKA_CITIES.get(origin, {"lat": 7.8731, "lng": 80.7718})
            dest_coords = SRI_LANKA_CITIES.get(destination, {"lat": 7.8731, "lng": 80.7718})

            # OSRM API request
            url = f"{base_url}{origin_coords['lng']},{origin_coords['lat']};{dest_coords['lng']},{dest_coords['lat']}?overview=false"
            response = requests.get(url)
            data = response.json()

            if response.status_code == 200 and data.get("code") == "Ok":
                route = data["routes"][0]
                routes.append({
                    "from": origin,
                    "to": destination,
                    "distance": f"{route['distance'] / 1000:.1f} km",
                    "duration": f"{int(route['duration'] / 60)} min"
                })
            else:
                routes.append({"from": origin, "to": destination, "error": "Route not found"})
        return {"routes": routes}
    except Exception as e:
        return {"error": f"Route error: {str(e)}"}

# Generate map HTML using Leaflet and OpenStreetMap
def generate_map_html(start: str, destinations: list, end: str) -> str:
    def get_coords(city: str) -> dict:
        default = {"lat": 7.8731, "lng": 80.7718}  # Center of Sri Lanka
        city_key = next((c for c in SRI_LANKA_CITIES if c.lower() == city.lower()), None)
        return SRI_LANKA_CITIES.get(city_key, default)

    # Prepare coordinates for all waypoints
    waypoints = [start] + destinations + [end]
    coords = [get_coords(city) for city in waypoints]
    coords_str = ",".join([f"[{c['lat']}, {c['lng']}]" for c in coords])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            #map {{ height: 400px; width: 100%; }}
        </style>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    </head>
    <body>
        <div id="map"></div>
        <script>
            var map = L.map('map').setView([7.8731, 80.7718], 8);
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            }}).addTo(map);
            var points = [{coords_str}];
            points.forEach(function(p) {{
                L.marker([p[0], p[1]]).addTo(map);
            }});
            L.polyline(points, {{color: 'blue'}}).addTo(map);
            map.fitBounds(L.polyline(points).getBounds());
        </script>
    </body>
    </html>
    """

# Extract destinations
def extract_destinations(plan: str, start: str, end: str) -> list:
    pattern = r"(?i)Day\s*\d+\s*:\s*([A-Za-z\s]+)(?:,|\s|\n|$)"
    matches = re.findall(pattern, plan)
    destinations = []
    seen = {start.lower(), end.lower()}
    for match in matches:
        city = match.strip()
        if city.lower() not in seen:
            city_key = next((c for c in SRI_LANKA_CITIES if c.lower() in city.lower()), city)
            destinations.append(city_key)
            seen.add(city.lower())
    return destinations

# Generate travel plan
def generate_travel_plan(user_input: str, budget: float, duration: int, interests: str, 
                        starting_location: str, end_location: str, travel_style: str) -> dict:
    if not all([starting_location.strip(), end_location.strip()]) or budget < 10000 or not (1 <= duration <= 14):
        return {"plan": "Invalid input: Check cities, budget (>10,000 LKR), or duration (1-14 days).", 
                "destinations": [], "routes": {}}
    
    inputs = {
        "user_input": user_input,
        "budget": budget,
        "duration": duration,
        "interests": interests,
        "history": memory.load_memory_variables({})["history"],
        "starting_location": starting_location,
        "end_location": end_location,
        "travel_style": travel_style
    }
    prompt = TRAVEL_PLAN_PROMPT.format(**inputs)
    with st.spinner("Generating itinerary..."):
        response = get_gemini_response(prompt)
    
    if "Error" not in response:
        memory.save_context({"user_input": user_input}, {"output": response})
    
    destinations = extract_destinations(response, starting_location, end_location)
    routes = get_route_details(starting_location, destinations, end_location)
    
    return {"plan": response, "destinations": destinations, "routes": routes}

# Generate meal plan
def generate_meal_ideas(duration: int) -> dict:
    meals = {
        "breakfast": [{"name": "String Hoppers", "price": "300-800 LKR"}, {"name": "Kiribath", "price": "200-500 LKR"}],
        "lunch": [{"name": "Rice and Curry", "price": "400-1200 LKR"}, {"name": "Kottu Roti", "price": "500-1000 LKR"}],
        "dinner": [{"name": "Crab Curry", "price": "1000-3000 LKR"}, {"name": "Deviled Chicken", "price": "700-1500 LKR"}]
    }
    daily = [{"day": d, "breakfast": random.choice(meals["breakfast"]), 
              "lunch": random.choice(meals["lunch"]), "dinner": random.choice(meals["dinner"])} 
             for d in range(1, duration + 1)]
    return {"daily_plan": daily}

# Generate phrasebook
def generate_phrasebook() -> dict:
    return {
        "greetings": [{"english": "Hello", "sinhala": "Ayubowan", "tamil": "Vanakkam"}],
        "essentials": [{"english": "How much?", "sinhala": "Meeka keeyada?", "tamil": "Idhu evvalavu?"}]
    }

# Suggest activities
def suggest_activities(interests: str) -> dict:
    activities = {
        "culture": [{"name": "Temple of the Tooth", "location": "Kandy", "cost": "2000 LKR"}],
        "beach": [{"name": "Whale Watching", "location": "Mirissa", "cost": "6000-12000 LKR"}]
    }
    interest_list = [i.strip().lower() for i in interests.split(",")]
    recommended = {k: v for k, v in activities.items() if any(i in k for i in interest_list)}
    return recommended or {"popular": activities["culture"]}

# Streamlit UI
def main():
    # Initialize session state
    defaults = {
        "plan": "",
        "destinations": [],
        "routes": {},
        "show_tips": True,
        "travel_style": "Mid-range",
        "start_date": None,
        "meal_ideas": None,
        "activity_suggestions": None,
        "progress": 0
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)

    # Minimal CSS
    st.markdown("""
        <style>
        .stButton>button { background: #1e5631; color: white; border-radius: 5px; }
        .stTabs [data-baseweb="tab"] { font-weight: bold; color: #1e5631; }
        </style>
    """, unsafe_allow_html=True)

    # Header
    st.title("SRI LANKA AI TRAVEL PLANNER")
    st.markdown("Welcome to Sri Lanka Travel Planner – your smart, personalized travel assistant powered by AI.")

    # Sidebar
    with st.sidebar:
        if st.button("Reset"):
            st.session_state.clear()
            memory.clear()
            st.session_state.update(defaults)
            st.rerun()
        
        st.checkbox("Show Tips", key="show_tips")
        st.session_state.travel_style = st.selectbox("Travel Style", ["Budget", "Mid-range", "Luxury"], 
                                                    index=["Budget", "Mid-range", "Luxury"].index(st.session_state.travel_style))
        
        st.progress(st.session_state.progress / 5)
        
        if st.session_state.plan:
            st.download_button(
                label="Download Plan",
                data=json.dumps({
                    "Itinerary": st.session_state.plan,
                    "Meals": st.session_state.meal_ideas,
                    "Phrasebook": generate_phrasebook()
                }, indent=2),
                file_name="sri_lanka_plan.json",
                mime="application/json"
            )

    # Tabs
    tabs = st.tabs(["Plan", "Map", "Activities", "Meals", "Phrasebook"])

    # Plan Tab
    with tabs[0]:
        if st.session_state.show_tips:
            st.markdown("- **Visa**: Apply for ETA online.\n- **Currency**: LKR, carry cash.\n- **Clothing**: Modest for temples.")
        
        with st.form("travel_form"):
            col1, col2 = st.columns(2)
            with col1:
                start_city = st.text_input("Start City", "Colombo")
                end_city = st.text_input("End City", "Colombo")
                duration = st.number_input("Days", 1, 14, 5, step=1)
            with col2:
                budget_usd = st.number_input("Budget (USD)", 50, 10000, 500, step=50)
                interests = st.text_input("Interests", "Culture, Beach")
                start_date = st.date_input("Start Date", datetime.now() + timedelta(days=7))
            
            user_input = st.text_area("Details", placeholder="E.g., vegetarian food")
            if st.form_submit_button("Generate"):
                st.session_state.progress = max(st.session_state.progress, 1)
                st.session_state.start_date = start_date
                result = generate_travel_plan(
                    user_input, budget_usd * EXCHANGE_RATE, duration, interests,
                    start_city, end_city, st.session_state.travel_style
                )
                st.session_state.update({
                    "plan": result["plan"],
                    "destinations": result["destinations"],
                    "routes": result["routes"],
                    "meal_ideas": generate_meal_ideas(duration),
                    "activity_suggestions": suggest_activities(interests)
                })
                st.success("Itinerary ready!")
        
        if st.session_state.plan:
            st.markdown(st.session_state.plan)

    # Map Tab
    with tabs[1]:
        st.session_state.progress = max(st.session_state.progress, 3)
        if st.session_state.destinations and st.session_state.routes.get("routes"):
            map_html = generate_map_html(
                st.session_state.routes["routes"][0].get("from", "Colombo"),
                st.session_state.destinations,
                st.session_state.routes["routes"][-1].get("to", "Colombo")
            )
            st.components.v1.html(map_html, height=400)
            for route in st.session_state.routes.get("routes", []):
                if "error" in route:
                    st.error(f"Error: {route['error']}")
                else:
                    st.markdown(f"**{route['from']} to {route['to']}**: {route['distance']}, ~{route['duration']}")
        else:
            st.warning("Generate itinerary first.")

    # Activities Tab
    with tabs[2]:
        if st.session_state.activity_suggestions:
            for cat, acts in st.session_state.activity_suggestions.items():
                st.subheader(cat.capitalize())
                for act in acts:
                    st.markdown(f"- **{act['name']}** ({act['location']}): {act['cost']}")
        else:
            st.warning("Generate itinerary first.")

    # Meals Tab
    with tabs[3]:
        if st.session_state.meal_ideas:
            for day in st.session_state.meal_ideas["daily_plan"]:
                st.markdown(f"**Day {day['day']}:**")
                for meal in ["breakfast", "lunch", "dinner"]:
                    st.markdown(f"- {meal.capitalize()}: {day[meal]['name']} ({day[meal]['price']})")
        else:
            st.warning("Generate itinerary first.")

    # Phrasebook Tab
    with tabs[4]:
        phrasebook = generate_phrasebook()
        for cat, phrases in phrasebook.items():
            st.subheader(cat.capitalize())
            for p in phrases:
                st.markdown(f"- **{p['english']}**: Sinhala: {p['sinhala']}, Tamil: {p['tamil']}")

if __name__ == "__main__":
    main()