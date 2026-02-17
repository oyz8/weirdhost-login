#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# scripts/weirdhost_renew.py

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import subprocess
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

from seleniumbase import SB

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

BASE_URL = "https://hub.weirdhost.xyz/server/"
DOMAIN = "hub.weirdhost.xyz"

# 续期阈值（天数），只有剩余时间小于此值才执行续期
RENEW_THRESHOLD_DAYS = int(os.environ.get("RENEW_THRESHOLD_DAYS", "2"))


# ============================================================
# 工具函数
# ============================================================
def mask_sensitive(text, show_chars=3):
    """脱敏处理敏感信息"""
    if not text:
        return "***"
    text = str(text)
    if len(text) <= show_chars * 2:
        return "*" * len(text)
    return text[:show_chars] + "*" * (len(text) - show_chars * 2) + text[-show_chars:]


def mask_email(email):
    """脱敏邮箱"""
    if not email or "@" not in email:
        return mask_sensitive(email)
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def mask_server_id(server_id):
    """脱敏服务器ID"""
    if not server_id:
        return "***"
    # 只显示前2位和后2位
    if len(server_id) <= 4:
        return "*" * len(server_id)
    return server_id[:2] + "*" * (len(server_id) - 4) + server_id[-2:]


def mask_url(url):
    """脱敏URL中的服务器ID"""
    if not url:
        return "***"
    if "/server/" in url:
        parts = url.split("/server/")
        if len(parts) == 2:
            return parts[0] + "/server/" + mask_server_id(parts[1])
    return url


def parse_accounts():
    """解析 ACCOUNTS 环境变量"""
    accounts_str = os.environ.get("ACCOUNTS", "").strip()
    if not accounts_str:
        return []
  
    try:
        accounts = json.loads(accounts_str)
        if not isinstance(accounts, list):
            print("[!] ACCOUNTS 格式错误：应为数组")
            return []
        return accounts
    except json.JSONDecodeError as e:
        print(f"[!] ACCOUNTS JSON 解析失败: {e}")
        return []


def parse_weirdhost_cookie(cookie_str):
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            return (parts[0].strip(), unquote(parts[1].strip()))
    return (None, None)


def build_server_url(server_id):
    if not server_id:
        return None
    server_id = server_id.strip()
    return server_id if server_id.startswith("http") else f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str):
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                diff = expiry_dt - datetime.now()
                if diff.total_seconds() < 0:
                    return "⚠️ 已过期"
                days = diff.days
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                parts = []
                if days > 0:
                    parts.append(f"{days}天")
                if hours > 0:
                    parts.append(f"{hours}小时")
                if minutes > 0 and days == 0:
                    parts.append(f"{minutes}分钟")
                return " ".join(parts) if parts else "不到1分钟"
            except ValueError:
                continue
        return "无法解析"
    except:
        return "计算失败"


def parse_expiry_to_datetime(expiry_str):
    if not expiry_str or expiry_str == "Unknown":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(expiry_str.strip(), fmt)
        except ValueError:
            continue
    return None


def get_remaining_days(expiry_str):
    """获取剩余天数（浮点数）"""
    expiry_dt = parse_expiry_to_datetime(expiry_str)
    if not expiry_dt:
        return None
    diff = expiry_dt - datetime.now()
    return diff.total_seconds() / 86400


def should_renew(expiry_str):
    """判断是否需要续期"""
    remaining_days = get_remaining_days(expiry_str)
    if remaining_days is None:
        return True
    return remaining_days <= RENEW_THRESHOLD_DAYS


def random_delay(min_sec=0.5, max_sec=2.0):
    time.sleep(random.uniform(min_sec, max_sec))


# ============================================================
# Telegram
# ============================================================
async def tg_notify(message):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
        except Exception as e:
            print(f"[TG] 发送失败: {e}")


async def tg_notify_photo(photo_path, caption=""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data)
        except Exception as e:
            print(f"[TG] 图片发送失败: {e}")


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


# ============================================================
# GitHub Secrets
# ============================================================
def encrypt_secret(public_key, secret_value):
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name, secret_value):
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo_token or not repository or not NACL_AVAILABLE:
        return False
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        try:
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                pk_data = await resp.json()
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            async with session.put(secret_url, headers=headers, json={
                "encrypted_value": encrypted_value, "key_id": pk_data["key_id"]
            }) as resp:
                return resp.status in (201, 204)
        except:
            return False


# ============================================================
# 页面解析
# ============================================================
def get_expiry_from_page(sb):
    """从页面获取到期时间"""
    try:
        page_text = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def is_logged_in(sb):
    """检查是否已登录"""
    try:
        url = sb.get_current_url()
        if "/login" in url or "/auth" in url:
            return False
        if get_expiry_from_page(sb) != "Unknown":
            return True
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
            return True
        return False
    except:
        return False


# ============================================================
# Turnstile 处理
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return 'no turnstile input';

    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }

    var turnstileContainers = document.querySelectorAll('[class*="sc-fKFyDc"], [class*="nwOmR"]');
    turnstileContainers.forEach(function(container) {
        container.style.overflow = 'visible';
        container.style.width = '300px';
        container.style.minWidth = '300px';
        container.style.height = '65px';
    });

    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });

    return 'done';
})();
"""


def check_turnstile_exists(sb):
    """检查页面是否有 Turnstile"""
    try:
        return sb.execute_script("""
            return document.querySelector('input[name="cf-turnstile-response"]') !== null;
        """)
    except:
        return False


def check_turnstile_solved(sb):
    """检查 Turnstile 是否已通过"""
    try:
        return sb.execute_script("""
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value && input.value.length > 20;
        """)
    except:
        return False


def get_turnstile_checkbox_coords(sb):
    """获取 Turnstile checkbox 的坐标"""
    try:
        coords = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('cloudflare') || src.includes('turnstile')) {
                    var rect = iframes[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                }
            }
        
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input) {
                var container = input.parentElement;
                for (var j = 0; j < 5; j++) {
                    if (!container) break;
                    var rect = container.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 30) {
                        return {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                    container = container.parentElement;
                }
            }
        
            return null;
        """)
        return coords
    except:
        return None


def activate_browser_window():
    """激活浏览器窗口"""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True, text=True, timeout=3
        )
        window_ids = result.stdout.strip().split('\n')
        if window_ids and window_ids[0]:
            subprocess.run(
                ["xdotool", "windowactivate", window_ids[0]],
                timeout=2, stderr=subprocess.DEVNULL
            )
            time.sleep(0.2)
            return True
    except:
        pass
    return False


def xdotool_click(x, y):
    """使用 xdotool 进行物理点击"""
    x, y = int(x), int(y)
  
    activate_browser_window()
  
    try:
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
  
    try:
        os.system(f"xdotool mousemove {x} {y} 2>/dev/null")
        time.sleep(0.15)
        os.system("xdotool click 1 2>/dev/null")
        return True
    except Exception:
        pass
  
    try:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")
        return True
    except Exception:
        return False


def click_turnstile_checkbox(sb):
    """使用 xdotool 点击 Turnstile checkbox"""
    coords = get_turnstile_checkbox_coords(sb)
    if not coords:
        print("[!] 无法获取 Turnstile 坐标")
        return False

    print(f"[*] Turnstile 位置: ({coords['x']:.0f}, {coords['y']:.0f}) "
          f"{coords['width']:.0f}x{coords['height']:.0f}")

    try:
        window_info = sb.execute_script("""
            return {
                screenX: window.screenX || 0,
                screenY: window.screenY || 0,
                outerHeight: window.outerHeight,
                innerHeight: window.innerHeight
            };
        """)
    
        chrome_bar_height = window_info["outerHeight"] - window_info["innerHeight"]
        abs_x = coords["click_x"] + window_info["screenX"]
        abs_y = coords["click_y"] + window_info["screenY"] + chrome_bar_height
    
        print(f"[*] 点击坐标: ({abs_x:.0f}, {abs_y:.0f})")
        return xdotool_click(abs_x, abs_y)
    except Exception as e:
        print(f"[!] 坐标计算失败: {e}")
        return False


# ============================================================
# 结果检测
# ============================================================

def check_result_popup(sb):
    """检测结果弹窗"""
    try:
        result = sb.execute_script("""
            var buttons = document.querySelectorAll('button');
            var hasNextBtn = false;
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].innerText.includes('NEXT') || buttons[i].innerText.includes('Next')) {
                    hasNextBtn = true;
                    break;
                }
            }
          
            var bodyText = document.body.innerText || '';
            var hasSuccessTitle = bodyText.includes('Success');
            var hasSuccessContent = bodyText.includes('성공') || 
                                    bodyText.includes('갱신') ||
                                    bodyText.includes('연장');
            var hasCooldown = bodyText.includes('아직') || 
                              bodyText.includes('Error');
          
            if (hasNextBtn || hasSuccessTitle) {
                if (hasCooldown && bodyText.includes('아직')) {
                    return 'cooldown';
                }
                if (hasSuccessTitle && hasSuccessContent) {
                    return 'success';
                }
                if (hasNextBtn) {
                    if (hasCooldown) return 'cooldown';
                    if (hasSuccessContent) return 'success';
                }
            }
          
            return null;
        """)
        return result
    except:
        return None


def check_popup_still_open(sb):
    """检查续期弹窗是否还在"""
    try:
        return sb.execute_script("""
            var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
            if (!turnstileInput) return false;
          
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var text = buttons[i].innerText || '';
                if (text.includes('시간추가') && !text.includes('DELETE')) {
                    var rect = buttons[i].getBoundingClientRect();
                    if (rect.x > 200 && rect.width > 0) {
                        return true;
                    }
                }
            }
            return false;
        """)
    except:
        return False


def click_next_button(sb):
    """点击 NEXT 按钮关闭结果弹窗"""
    try:
        next_selectors = [
            "//button[contains(text(), 'NEXT')]",
            "//button[contains(text(), 'Next')]",
            "//button//span[contains(text(), 'NEXT')]",
        ]
        for sel in next_selectors:
            if sb.is_element_visible(sel):
                sb.click(sel)
                print("[+] 已点击 NEXT 按钮")
                return True
    except:
        pass
    return False


# ============================================================
# 续期弹窗处理
# ============================================================

def handle_renewal_popup(sb, screenshot_prefix="", timeout=90):
    """处理续期弹窗流程"""
    screenshot_name = f"{screenshot_prefix}_popup.png" if screenshot_prefix else "popup_fixed.png"

    print("\n[阶段1] 等待弹窗和 Turnstile...")

    turnstile_ready = False
    for _ in range(20):
        result = check_result_popup(sb)
        if result == "cooldown":
            print("[*] 检测到冷却期弹窗")
            sb.save_screenshot(screenshot_name)
            return {"status": "cooldown", "screenshot": screenshot_name}
        if result == "success":
            print("[+] 检测到成功弹窗")
            sb.save_screenshot(screenshot_name)
            return {"status": "success", "screenshot": screenshot_name}
      
        if check_turnstile_exists(sb):
            turnstile_ready = True
            print("[+] 检测到 Turnstile")
            break
    
        time.sleep(1)

    if not turnstile_ready:
        print("[!] 未检测到 Turnstile")
        sb.save_screenshot(screenshot_name)
        return {"status": "error", "message": "未检测到 Turnstile", "screenshot": screenshot_name}

    print("\n[阶段2] 修复弹窗样式...")

    for _ in range(3):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.5)

    sb.save_screenshot(screenshot_name)

    print("\n[阶段3] 点击 Turnstile 并等待结果...")

    for attempt in range(6):
        print(f"\n  --- 尝试 {attempt + 1}/6 ---")
    
        if check_turnstile_solved(sb):
            print("[+] Turnstile 已通过!")
            break
    
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
    
        click_turnstile_checkbox(sb)
    
        print("[*] 等待 Turnstile 验证...")
        for _ in range(8):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                print("[+] Turnstile 已通过!")
                break
    
        if check_turnstile_solved(sb):
            break
    
        sb.save_screenshot(f"{screenshot_prefix}_turnstile_{attempt}.png" if screenshot_prefix else f"turnstile_attempt_{attempt}.png")
  
    print("\n[*] 等待自动提交和结果弹窗...")
  
    result_timeout = 45
    result_start = time.time()
    last_screenshot_time = 0

    while time.time() - result_start < result_timeout:
        result = check_result_popup(sb)
      
        if result == "success":
            print("[+] 续期成功!")
            sb.save_screenshot(screenshot_name)
            time.sleep(1)
            click_next_button(sb)
            return {"status": "success", "screenshot": screenshot_name}
      
        if result == "cooldown":
            print("[*] 冷却期内")
            sb.save_screenshot(screenshot_name)
            time.sleep(1)
            click_next_button(sb)
            return {"status": "cooldown", "screenshot": screenshot_name}
      
        if not check_popup_still_open(sb):
            print("[*] 弹窗已消失，检查结果...")
            time.sleep(2)
          
            result = check_result_popup(sb)
            if result:
                sb.save_screenshot(screenshot_name)
                if result == "success":
                    print("[+] 续期成功!")
                    click_next_button(sb)
                    return {"status": "success", "screenshot": screenshot_name}
                elif result == "cooldown":
                    print("[*] 冷却期内")
                    click_next_button(sb)
                    return {"status": "cooldown", "screenshot": screenshot_name}
      
        if time.time() - last_screenshot_time > 5:
            sb.save_screenshot(screenshot_name)
            last_screenshot_time = time.time()
            print(f"[*] 等待中... ({int(time.time() - result_start)}s)")
    
        time.sleep(1)

    print("[!] 等待结果超时")
    sb.save_screenshot(screenshot_name)
    return {"status": "timeout", "screenshot": screenshot_name}


# ============================================================
# Cookie 更新检查（可视化版）
# ============================================================

def check_and_update_cookie(sb, cookie_env, original_cookie_value):
    """检查并更新 Cookie（带可视化输出）"""
    print("\n[Cookie检查] 开始检查 Cookie 变化...")
  
    try:
        cookies = sb.get_cookies()
        new_cookie_found = False
      
        for cookie in cookies:
            if cookie.get("name", "").startswith("remember_web"):
                new_val = cookie.get("value", "")
                cookie_name = cookie.get("name", "")
              
                # 显示当前获取到的 Cookie（脱敏）
                print(f"[Cookie检查] 当前 Cookie: {cookie_name[:20]}...{cookie_name[-10:]}")
                print(f"[Cookie检查] 原值: ...{original_cookie_value[-20:]}")
                print(f"[Cookie检查] 新值: ...{new_val[-20:] if new_val else 'N/A'}")
              
                if new_val and new_val != original_cookie_value:
                    new_cookie_found = True
                    new_cookie_str = f"{cookie_name}={new_val}"
                  
                    print(f"[Cookie检查] ⚡ 检测到 Cookie 变化!")
                    print(f"[Cookie检查] 正在更新 GitHub Secret: {cookie_env}...")
                  
                    if asyncio.run(update_github_secret(cookie_env, new_cookie_str)):
                        print(f"[Cookie检查] ✅ {cookie_env} 已成功更新到 GitHub Secrets")
                        return True
                    else:
                        print(f"[Cookie检查] ❌ {cookie_env} 更新失败")
                        return False
                else:
                    print(f"[Cookie检查] ℹ️ Cookie 未变化，无需更新")
                break
      
        if not new_cookie_found:
            print(f"[Cookie检查] ℹ️ 未检测到 remember_web Cookie 或无变化")
          
    except Exception as e:
        print(f"[Cookie检查] ❌ 检查失败: {e}")
  
    return False


# ============================================================
# 单账号处理（优化版）
# ============================================================

def process_single_account(sb, account, account_index):
    """处理单个账号 - 优化版"""
    remark = account.get("remark", f"账号{account_index + 1}")
    server_id = account.get("id", "").strip()
    cookie_env = account.get("cookie_env", "").strip()
  
    # 对 remark 进行脱敏处理（用于日志输出）
    display_name = mask_email(remark) if "@" in remark else remark
  
    result = {
        "remark": remark,              # 完整值用于 Telegram 报告
        "display_name": display_name,  # 脱敏后用于日志
        "server_id": server_id,        # 服务器ID用于 Telegram 报告
        "cookie_env": cookie_env,
        "status": "unknown",
        "original_expiry": "Unknown",
        "new_expiry": "Unknown",
        "message": "",
        "screenshot": None,
        "cookie_updated": False,
        "skipped": False
    }
  
    print(f"\n{'=' * 60}")
    print(f"处理账号 [{account_index + 1}]: {display_name}")
    print(f"{'=' * 60}")
  
    # ===== 验证配置 =====
    if not server_id:
        print(f"[!] 账号 {display_name}: 缺少 id")
        result["status"] = "error"
        result["message"] = "缺少 id"
        return result
  
    if not cookie_env:
        print(f"[!] 账号 {display_name}: 缺少 cookie_env")
        result["status"] = "error"
        result["message"] = "缺少 cookie_env"
        return result
  
    # 获取 Cookie
    cookie_str = os.environ.get(cookie_env, "").strip()
    if not cookie_str:
        print(f"[!] 账号 {display_name}: 环境变量 {cookie_env} 未设置")
        result["status"] = "error"
        result["message"] = f"{cookie_env} 未设置"
        return result
  
    cookie_name, cookie_value = parse_weirdhost_cookie(cookie_str)
    server_url = build_server_url(server_id)
  
    if not cookie_name or not cookie_value:
        print(f"[!] 账号 {display_name}: Cookie 格式错误")
        result["status"] = "error"
        result["message"] = "Cookie 格式错误"
        return result
  
    if not cookie_name.startswith("remember_web"):
        print(f"[!] 账号 {display_name}: Cookie 名称错误")
        result["status"] = "error"
        result["message"] = f"Cookie 名称错误"
        return result
  
    # 脱敏显示
    masked_server_id = mask_server_id(server_id)
    masked_url = mask_url(server_url)
  
    print(f"[*] 环境变量: {cookie_env}")
    print(f"[*] 服务器ID: {masked_server_id}")
    print(f"[*] URL: {masked_url}")
  
    screenshot_prefix = f"account_{account_index + 1}"
  
    try:
        # ===== 步骤1: 清除旧 Cookie 并设置新 Cookie =====
        print("\n[步骤1] 设置 Cookie")
      
        activate_browser_window()
      
        try:
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(1)
            sb.delete_all_cookies()
        except:
            pass
      
        sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
        time.sleep(2)
        sb.add_cookie({
            "name": cookie_name, "value": cookie_value,
            "domain": DOMAIN, "path": "/"
        })
        print("[+] Cookie 已设置")

        # ===== 步骤2: 访问服务器页面获取到期时间 =====
        print("\n[步骤2] 获取到期时间")
        sb.uc_open_with_reconnect(server_url, reconnect_time=5)
        time.sleep(3)

        if not is_logged_in(sb):
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)

        if not is_logged_in(sb):
            screenshot_path = f"{screenshot_prefix}_login_failed.png"
            sb.save_screenshot(screenshot_path)
            result["status"] = "error"
            result["message"] = "Cookie 失效"
            result["screenshot"] = screenshot_path
            return result

        print("[+] 登录成功")

        # 获取到期时间
        original_expiry = get_expiry_from_page(sb)
        remaining = calculate_remaining_time(original_expiry)
        remaining_days = get_remaining_days(original_expiry)
        result["original_expiry"] = original_expiry
      
        print(f"[*] 到期: {original_expiry}")
        print(f"[*] 剩余: {remaining}")
        if remaining_days is not None:
            print(f"[*] 剩余天数: {remaining_days:.2f} 天")

        # ===== 步骤3: 判断是否需要续期 =====
        print(f"\n[步骤3] 检查是否需要续期 (阈值: {RENEW_THRESHOLD_DAYS} 天)")
      
        need_renew = should_renew(original_expiry)
      
        if not need_renew:
            print(f"[*] 剩余 {remaining_days:.2f} 天 > {RENEW_THRESHOLD_DAYS} 天，跳过续期")
            result["status"] = "skipped"
            result["skipped"] = True
            result["new_expiry"] = original_expiry
            result["message"] = f"剩余 {remaining_days:.1f} 天，无需续期"
          
            # 检查并更新 Cookie（可视化）
            if check_and_update_cookie(sb, cookie_env, cookie_value):
                result["cookie_updated"] = True
          
            return result
      
        # 修复：先计算显示值，再放入 f-string
        remaining_display = f"{remaining_days:.2f}" if remaining_days else "?"
        print(f"[+] 剩余 {remaining_display} 天 <= {RENEW_THRESHOLD_DAYS} 天，执行续期")

        # ===== 步骤4: 点击侧栏续期按钮 =====
        print("\n[步骤4] 点击侧栏续期按钮")
        random_delay(1.0, 2.0)

        sidebar_btn_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
        if not sb.is_element_present(sidebar_btn_xpath):
            sidebar_btn_xpath = "//button[contains(., '시간추가')]"
      
        if not sb.is_element_present(sidebar_btn_xpath):
            screenshot_path = f"{screenshot_prefix}_no_button.png"
            sb.save_screenshot(screenshot_path)
            result["status"] = "error"
            result["message"] = "未找到续期按钮"
            result["screenshot"] = screenshot_path
            return result

        sb.click(sidebar_btn_xpath)
        print("[+] 已点击侧栏按钮，等待弹窗...")
        time.sleep(3)

        # ===== 步骤5: 处理续期弹窗 =====
        print("\n[步骤5] 处理续期弹窗")
        popup_result = handle_renewal_popup(sb, screenshot_prefix=screenshot_prefix, timeout=90)
        print(f"\n[*] 处理结果: {popup_result['status']}")
      
        result["screenshot"] = popup_result.get("screenshot")

        # ===== 步骤6: 验证续期结果 =====
        print("\n[步骤6] 验证续期结果")
        time.sleep(3)
      
        sb.uc_open_with_reconnect(server_url, reconnect_time=3)
        time.sleep(3)
      
        new_expiry = get_expiry_from_page(sb)
        result["new_expiry"] = new_expiry

        print(f"[*] 原到期: {original_expiry}")
        print(f"[*] 新到期: {new_expiry}")

        original_dt = parse_expiry_to_datetime(original_expiry)
        new_dt = parse_expiry_to_datetime(new_expiry)

        if popup_result["status"] == "cooldown":
            result["status"] = "cooldown"
            result["message"] = "冷却期内"
        elif original_dt and new_dt and new_dt > original_dt:
            diff_h = (new_dt - original_dt).total_seconds() / 3600
            result["status"] = "success"
            result["message"] = f"延长了 {diff_h:.1f} 小时"
            print(f"\n[+] 成功！延长 {diff_h:.1f} 小时")
        elif popup_result["status"] == "success":
            result["status"] = "success"
            result["message"] = "操作完成"
        else:
            result["status"] = popup_result["status"]
            result["message"] = popup_result.get("message", "未知状态")

        # ===== 步骤7: 更新 Cookie（可视化）=====
        print("\n[步骤7] 检查 Cookie 更新")
        if check_and_update_cookie(sb, cookie_env, cookie_value):
            result["cookie_updated"] = True

    except Exception as e:
        import traceback
        print(f"\n[!] 账号 {display_name} 处理异常: {repr(e)}")
        traceback.print_exc()
        result["status"] = "error"
        result["message"] = str(e)
  
    return result


# ============================================================
# 汇总报告（完整信息版）
# ============================================================

def send_summary_report(results):
    """发送汇总报告到 Telegram（完整信息版）"""
    success_count = sum(1 for r in results if r["status"] == "success")
  # cooldown_count = sum(1 for r in results if r["status"] == "cooldown")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
  # error_count = sum(1 for r in results if r["status"] in ["error", "timeout", "unknown"])
    error_count = sum(1 for r in results if r["status"] in ["error", "timeout", "unknown", "cooldown"])  # 加上 cooldow 万一有冷却：会被统计到失败数量里，不会漏掉
  
    lines = [
        "🎁 <b>Weirdhost 多账号续期报告</b>",
        "",
        f"📊 共 {len(results)} 个账号",
      # f"✅ 成功: {success_count}  ⏭️ 跳过: {skipped_count}  ⏳ 冷却: {cooldown_count}  ❌ 失败: {error_count}",
        f"✅ 成功: {success_count}  ⏭️ 跳过: {skipped_count}  ❌ 失败: {error_count}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
  
    for i, r in enumerate(results):
        status_icon = {
            "success": "✅",
            "cooldown": "⏳",
            "skipped": "⏭️",
            "error": "❌",
            "timeout": "⚠️"
        }.get(r["status"], "❓")
      
        cookie_env = r.get("cookie_env", "")
        cookie_updated = r.get("cookie_updated", False)
        server_id = r.get("server_id", "")
      
        # Telegram 报告使用完整的 remark（不脱敏）
        remark = r.get("remark", f"账号{i+1}")
      
        lines.append(f"\n{status_icon} <b>{remark}</b>")
      
        # 显示服务器ID
        if server_id:
            lines.append(f"   🖥️ 服务器: {server_id}")
      
        if r["status"] == "success":
            lines.append(f"   📅 到期: {r['new_expiry']}")
            lines.append(f"   ⏳ 剩余: {calculate_remaining_time(r['new_expiry'])}")
            if r.get("message"):
                lines.append(f"   📝 {r['message']}")
            if cookie_env:
                if cookie_updated:
                    lines.append(f"   🔑 Cookie: ✅ 已自动更新")
                else:
                    lines.append(f"   🔑 Cookie: 无变化")
      
        elif r["status"] == "skipped":
            lines.append(f"   📅 到期: {r['original_expiry']}")
            lines.append(f"   ⏳ 剩余: {calculate_remaining_time(r['original_expiry'])}")
            lines.append(f"   💡 {r.get('message', '无需续期')}")
            if cookie_env:
                if cookie_updated:
                    lines.append(f"   🔑 Cookie: ✅ 已自动更新")
                else:
                    lines.append(f"   🔑 Cookie: 无变化")
                  
        elif r["status"] == "cooldown":
            lines.append(f"   📅 到期: {r['original_expiry']}")
            lines.append(f"   ⏳ 剩余: {calculate_remaining_time(r['original_expiry'])}")
            lines.append(f"   💡 冷却期内，暂时无法续期")
            if cookie_env:
                if cookie_updated:
                    lines.append(f"   🔑 Cookie: ✅ 已自动更新")
                else:
                    lines.append(f"   🔑 Cookie: 无变化")
                  
        else:
            lines.append(f"   ⚠️ {r.get('message', '未知错误')}")
            if cookie_env:
                if cookie_updated:
                    lines.append(f"   🔑 Cookie: ✅ 已自动更新")
  
    message = "\n".join(lines)
  
    # 只有在有续期操作时才发送截图
    screenshot = None
    for r in results:
        if r["status"] in ["success", "cooldown", "error", "timeout"]:
            if r.get("screenshot") and os.path.exists(r["screenshot"]):
                screenshot = r["screenshot"]
                break
  
    if screenshot:
        sync_tg_notify_photo(screenshot, message)
    else:
        sync_tg_notify(message)

# ============================================================
# 主函数
# ============================================================

def add_server_time():
    """主函数 - 多账号版本（优化版）"""
    accounts = parse_accounts()
  
    if not accounts:
        sync_tg_notify("🎁 <b>Weirdhost 多账号续期</b>\n\n❌ ACCOUNTS 未设置或格式错误\n\n请设置 ACCOUNTS 环境变量，格式为 JSON 数组")
        return
  
    print("=" * 60)
    print(f"Weirdhost 自动续期 v15 (隐私保护版)")
    print(f"共 {len(accounts)} 个账号")
    print(f"续期阈值: {RENEW_THRESHOLD_DAYS} 天")
    print("=" * 60)
  
    results = []
  
    try:
        with SB(uc=True, test=True, locale="ko", headless=False) as sb:
            print("\n[*] 浏览器已启动")
          
            for i, account in enumerate(accounts):
                result = process_single_account(sb, account, i)
                results.append(result)
              
                # 如果是跳过的账号，等待时间更短
                if i < len(accounts) - 1:
                    if result.get("skipped"):
                        wait_time = random.randint(2, 4)
                    else:
                        wait_time = random.randint(5, 10)
                    print(f"\n[*] 等待 {wait_time} 秒后处理下一个账号...")
                    time.sleep(wait_time)
  
    except Exception as e:
        import traceback
        print(f"\n[!] 浏览器异常: {repr(e)}")
        traceback.print_exc()
      
        if results:
            send_summary_report(results)
        else:
            sync_tg_notify(f"🎁 <b>Weirdhost</b>\n\n❌ 浏览器启动失败\n\n<code>{repr(e)}</code>")
        return
  
    send_summary_report(results)


if __name__ == "__main__":
    add_server_time()
