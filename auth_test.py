import os, streamlit as st
import streamlit_authenticator as stauth

st.set_page_config("Auth Test")

# Minimal fallback creds (use your bcrypt hash)
admin_user = os.getenv("APP_ADMIN_USER", "admin")
admin_hash = os.getenv("APP_ADMIN_HASH", "$2b$12$replace_me_with_real_hash")
creds = {"usernames": {admin_user: {"name": "Administrator", "password": admin_hash}}}

authenticator = stauth.Authenticate(
    credentials=creds,
    cookie_name="auth_test_cookie",
    cookie_key="super-secret-key",
    cookie_expiry_days=1,
)

#st.write("DEBUG: About to render login form")
name, status, username = authenticator.login(
    fields={"Form name": "Login", "Username": "Username", "Password": "Password"},
    location="main",
)

if status:
    st.success(f"Logged in as {name}")
    authenticator.logout("Logout", "sidebar")
else:
    st.info("Not logged in yet.")