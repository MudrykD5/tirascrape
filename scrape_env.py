import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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
        print(f"[SKIPPED] Invalid registration number: {reg_number}")
        return {
            "plate": reg_number,
            "status": "Invalid",
            "Start Date": None,
            "End Date": None,
            "Transacting Company": None
        }

    data = {"plate": reg_number}

    try:
        # Click radio button
        page.wait_for_selector("label[for='mat-radio-3-input']", timeout=15000)
        try:
            page.locator("div.cdk-overlay-container").evaluate("el => el.style.display='none'")
        except Exception:
            pass
        page.click("label[for='mat-radio-3-input']")

        # Fill registration number and click verify
        page.fill("input[placeholder='Enter Registration Number']", str(reg_number))
        page.click("button:has-text('VERIFY')")

        # Wait for latest dialog
        dialog = wait_for_latest_dialog(page, timeout=15000)

        # --- NOT FOUND ---
        if dialog.locator(f"b:has-text('{STATUS_TEXTS['not_found']}')").is_visible():
            data.update({"status": "Not Found", "Start Date": None, "End Date": None, "Transacting Company": None})
            print(f"[SUCCESS] {reg_number} → Not Found")
            close_dialog(dialog)
            return data

        # --- ACTIVE ---
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
            print(f"[SUCCESS] {reg_number} → Active")
            close_dialog(dialog)
            return data

        # --- EXPIRED ---
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
            print(f"[SUCCESS] {reg_number} → Expired")
            close_dialog(dialog)
            return data

        # --- UNKNOWN ---
        data.update({"status": "Unknown", "Start Date": None, "End Date": None, "Transacting Company": None})
        print(f"[WARN] {reg_number} → Unknown status")
        close_dialog(dialog)
        return data

    except Exception as e:
        data.update({"status": "Error", "Start Date": None, "End Date": None, "Transacting Company": None})
        print(f"[ERROR] {reg_number} scraping failed: {e}")
        return data

def main():
    df = pd.read_csv('data_chunks/chunk_2.csv')
    df['Car Registration'] = df['Car Registration'].astype(str).str.strip()

    results_list = []

    with sync_playwright() as p:
        for reg in df['Car Registration']:
            # Fresh browser for each plat
            browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto("https://tiramis.tira.go.tz", wait_until="domcontentloaded")

            result = check_insurance(page, reg)
            results_list.append(result)

            browser.close()

    results_df = pd.DataFrame(results_list)
    df = pd.concat([df, results_df[["status", "Start Date", "End Date", "Transacting Company"]]], axis=1)

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"data_with_new_cols_{timestamp}.csv"
    df.to_csv(output_filename, index=False)

    print("Scraping complete!")

if __name__ == "__main__":

    main()                                                                                                                                                   


