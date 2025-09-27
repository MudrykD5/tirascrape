# -------------------------------
# Imports from both scripts
# -------------------------------
import os
import pandas as pd
from datetime import datetime, timedelta
import requests
from supabase import create_client, Client
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------------
# Constants from scraper script
# -------------------------------
DIALOG_CONTAINER_SELECTOR = "mat-dialog-container[role='dialog']"

STATUS_TEXTS = {
    "active": "INSURANCE IS ACTIVE",
    "expired": "INSURANCE EXPIRED",
    "not_found": "INSURANCE NOT FOUND!",
}

CLOSE_BUTTON_SELECTORS = [
    "button[mat-dialog-close]",
    "button:has-text('OK')",
    "button:has-text('Close')",
    "button:has-text('CLOSE')",
    "button:has-text('Done')",
    "button:has-text('Cancel')",
]

# -------------------------------
# Scraper functions (unchanged)
# -------------------------------
def wait_for_latest_dialog(page, timeout=15000):
    containers = page.locator(DIALOG_CONTAINER_SELECTOR)
    containers.first.wait_for(state="attached", timeout=timeout)
    latest = containers.last
    latest.wait_for(state="visible", timeout=timeout)
    return latest

def close_dialog(dialog):
    for sel in CLOSE_BUTTON_SELECTORS:
        try:
            btns = dialog.locator(sel)
            if btns.count() > 0 and btns.first.is_visible():
                btns.first.click()
                return
        except Exception:
            continue
    try:
        dialog.page.keyboard.press("Escape")
    except Exception:
        pass

def safe_get_field(dialog, label: str):
    try:
        locator = dialog.locator(f"strong:has-text('{label}')").locator("xpath=../following-sibling::*")
        if locator.count() > 0:
            return locator.first.inner_text().strip()
    except Exception:
        pass
    return None

def check_insurance(page, reg_number: str) -> dict:
    if not reg_number or str(reg_number).strip().lower() == 'nan':
        return {
            "plate": reg_number,
            "status": "Invalid",
            "Start Date": None,
            "End Date": None,
            "Transacting Company": None
        }

    data = {"plate": reg_number}

    try:
        page.wait_for_selector("label[for='mat-radio-3-input']", timeout=15000)
        try:
            page.locator("div.cdk-overlay-container").evaluate("el => el.style.display='none'")
        except Exception:
            pass
        page.click("label[for='mat-radio-3-input']")
        page.fill("input[placeholder='Enter Registration Number']", str(reg_number))
        page.click("button:has-text('VERIFY')")

        dialog = wait_for_latest_dialog(page, timeout=15000)

        if dialog.locator(f"b:has-text('{STATUS_TEXTS['not_found']}')").is_visible():
            data.update({"status": "Not Found", "Start Date": None, "End Date": None, "Transacting Company": None})
            close_dialog(dialog)
            return data

        if dialog.locator(f"b:has-text('{STATUS_TEXTS['active']}')").is_visible():
            data["status"] = "Active"
            try:
                reg_text = dialog.locator("h2").inner_text()
                data["registration_no"] = reg_text.replace("Registration No. ", "").strip()
            except Exception:
                data["registration_no"] = None
            data["Start Date"] = safe_get_field(dialog, "Start Date")
            data["End Date"] = safe_get_field(dialog, "End Date")
            data["Transacting Company"] = safe_get_field(dialog, "Transacting Company")
            close_dialog(dialog)
            return data

        if dialog.locator(f"b:has-text('{STATUS_TEXTS['expired']}')").is_visible():
            data["status"] = "Expired"
            try:
                reg_text = dialog.locator("h2").inner_text()
                data["registration_no"] = reg_text.replace("Registration No. ", "").strip()
            except Exception:
                data["registration_no"] = None
            try:
                panel_header = dialog.locator("mat-expansion-panel-header")
                if panel_header.is_visible():
                    panel_header.click()
                    dialog.wait_for_selector("mat-expansion-panel .mat-expansion-panel-body strong", timeout=3000)
            except Exception:
                pass
            data["Start Date"] = safe_get_field(dialog, "Start Date")
            data["End Date"] = safe_get_field(dialog, "End Date")
            data["Transacting Company"] = safe_get_field(dialog, "Transacting Company")
            close_dialog(dialog)
            return data

        data.update({"status": "Unknown", "Start Date": None, "End Date": None, "Transacting Company": None})
        close_dialog(dialog)
        return data

    except Exception as e:
        data.update({"status": "Error", "Start Date": None, "End Date": None, "Transacting Company": None})
        return data

# -------------------------------
# Supabase + SMS reminder script (unchanged)
# -------------------------------
SUPABASE_URL = "https://rotofclunfwrociddxht.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

REMINDER_OFFSETS = [
    {"days": 30, "field": "30_days_before"},
    {"days": 15, "field": "15_days_before"},
    {"days": 7, "field": "15_days_before"},
    {"days": 0, "field": "d_day"},
    {"days": -7, "field": "15_days_after"},
    {"days": -15, "field": "15_days_after"},
]

NEXTSMS_API_KEY = os.getenv("NEXTSMS_API_KEY")

def send_sms(api_key, sender_id, phone_number, text):
    url = "https://messaging-service.co.tz/api/sms/v1/text/single"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {api_key}"
    }
    payload = {
        "from": sender_id,
        "to": phone_number,
        "text": text
    }
    response = requests.post(url, json=payload, headers=headers)
    result = response.json()
    if not response.ok:
        raise Exception(result.get("message", "SMS sending failed"))
    return result

# -------------------------------
# Date normalization helpers
# -------------------------------
def normalize_supabase_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None

def normalize_tira_date(date_str):
    try:
        return datetime.strptime(date_str, "%d %b, %Y %I:%M:%S %p").date()
    except Exception:
        return None

# -------------------------------
# Unified handler: connection logic
# -------------------------------
def handler():
    today = datetime.now().date()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        for offset in REMINDER_OFFSETS:
            reminder_date = today + timedelta(days=offset["days"])
            formatted_date = reminder_date.isoformat()

            response = (
                supabase
                .from_("customers")
                .select(
                    f"""
                    full_name,
                    phone_number,
                    car_registration,
                    insurance_expiry_date,
                    uuid,
                    agent_email,
                    agents!customers_uuid_fkey(
                        api_key,
                        sender_id,
                        messagetemplates!messagetemplates_uuid_fkey(
                            {offset["field"]}
                        )
                    )
                    """
                )
                .eq("insurance_expiry_date", formatted_date)
                .execute()
            )

            customers = response.data
            if not customers:
                print(f"ℹ️ No reminders needed for {formatted_date}")
                continue

            for cust in customers:
                reg = cust.get("car_registration")
                supabase_expiry = cust.get("insurance_expiry_date")

                # --- NEW CONNECTION: validate against TIRA MIS ---
                tira_result = check_insurance(page, reg)
                tira_expiry = tira_result.get("End Date")
                print('tira result::;',tira_result,'tira expiry:',tira_expiry)
                supabase_date = normalize_supabase_date(supabase_expiry) if supabase_expiry else None
                tira_date = normalize_tira_date(tira_expiry) if tira_expiry else None

                if not supabase_date or not tira_date or supabase_date != tira_date:
                    print(f"⚠️ Skipping {reg}: Supabase expiry {supabase_expiry} != TIRA expiry {tira_expiry}")
                    continue

                # If expiry matches, proceed with SMS send (unchanged)
                agent = cust.get("agents")
                template = agent.get("messagetemplates")[0].get(offset["field"]) if agent else None
                if not agent or not agent.get("api_key") or not agent.get("sender_id") or not template:
                    print(f"⚠️ Skipping {cust.get('phone_number')}: missing agent credentials or template")
                    continue

                first_name = cust.get("full_name", "").split(" ")[0] if cust.get("full_name") else ""
                message = (
                    template
                    .replace("{CustomerFirstName}", first_name)
                    .replace("{CarRegistration}", reg or "")
                    .replace("{RenewalDate}", supabase_expiry or "")
                )

                try:
                    send_sms(agent["api_key"], agent["sender_id"], cust["phone_number"], message)
                    print(f"✅ Sent to {cust['phone_number']} [{offset['field']}]")
                except Exception as e:
                    print(f"❌ SMS failed for {cust['phone_number']}: {e}")

        browser.close()

    return {"status": "reminders complete"}


if __name__ == "__main__":
    handler()
