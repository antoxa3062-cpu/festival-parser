# -*- coding: utf-8 -*-
"""Сбор участников фестиваля по куратору (GetCourse).
Версия 11: все личные/сайтовые значения — в .env; в коде только заглушки.
"""

import csv
import os
import re
import sys
from collections import Counter
from datetime import datetime
from html import escape
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
except ImportError:
    print("Не установлена библиотека python-dotenv.")
    print("Выполни в терминале: pip install python-dotenv")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))
GC_LOGIN    = os.getenv("GC_LOGIN", "").strip()
GC_PASSWORD = os.getenv("GC_PASSWORD", "").strip()

# ================== НАСТРОЙКИ (личные значения — в .env) ==================
BASE_URL   = os.getenv("GC_BASE_URL", "https://your-school.getcourse.ru").rstrip("/")  # ← ИЗМЕНЕНО
CURATOR    = os.getenv("GC_CURATOR", "Имя куратора")                                   # ← ИЗМЕНЕНО
TRAINING   = os.getenv("GC_TRAINING", "Название тренинга")                             # ← ИЗМЕНЕНО
MARKER_SRC = os.getenv("GC_MARKER_SRC", "")                                            # ← ИЗМЕНЕНО

def _parse_lesson_ids(raw):
    """Читает id уроков: по одному на строку; всё после # — комментарий; пустые строки пропускаем."""
    ids = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()   # отбрасываем комментарий и пробелы
        if not line:
            continue
        for part in line.split(","):           # заодно поддерживаем формат "id,id,id"
            part = part.strip()
            if part:
                ids.append(int(part))
    return ids


LESSON_IDS = _parse_lesson_ids(os.getenv("GC_LESSON_IDS", ""))

URL_TEMPLATE = BASE_URL + "/pl/teach/control/lesson/view?id={lesson_id}&editMode=0"    # ← ИЗМЕНЕНО

USER_DATA_DIR = os.path.join(BASE_DIR, "gc_profile")
OUT_CSV     = os.path.join(BASE_DIR, "participants.csv")
OUT_WAITING = os.path.join(BASE_DIR, "waiting_reply.csv")
OUT_HTML    = os.path.join(BASE_DIR, "curator_report.html")
DEBUG_HTML  = os.path.join(BASE_DIR, "debug_page.html")
MAX_CLICKS  = 60
# ===============================================

CURATOR_RE = re.compile(r"[\(\[]куратор[\)\]]", re.I)
THANKS_WORDS_RE = re.compile(r"спасибо|благодар", re.I)
THANKS_EMOJI_RE = re.compile(r"[🙏❤💖💗🤍💚💛💙💜🧡⭐😍💓💕]")


def auto_login(page, login=None, password=None):
    """Авто-вход на GetCourse по логину/паролю из .env."""
    login = login or GC_LOGIN
    password = password or GC_PASSWORD
    if not login or not password:
        print("  ! Не заданы GC_LOGIN/GC_PASSWORD в .env — авто-вход невозможен")
        return False

    print(f"  >>> Авто-вход как {login}...")
    try:
        page.wait_for_timeout(2000)

        # Если мы не на странице входа — идём на страницу логина школы
        if not re.search(r"(login|sign|enter|auth)", page.url, re.I):
            page.goto(BASE_URL + "/cms/system/login?required=true",          # ← ИЗМЕНЕНО
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

        pwd = page.locator("input[type='password']").first
        pwd.wait_for(state="visible", timeout=15000)

        login_input = page.locator(
            "input[type='email']:visible, "
            "input[type='text']:visible, "
            "input:not([type]):visible"
        ).first
        login_input.wait_for(state="visible", timeout=10000)

        login_input.fill(login)
        pwd.fill(password)

        btn = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button:has-text('Войти'), button:has-text('Log in'), "
            "button:has-text('Sign in')"
        ).first
        btn.click()

        page.wait_for_selector("input[type='password']", state="detached", timeout=30000)
        page.wait_for_timeout(2000)
        print("  >>> Авто-вход выполнен успешно")
        return True
    except Exception as e:
        print(f"  ! Авто-вход не удался: {type(e).__name__}: {e}")
        try:
            page.screenshot(path=os.path.join(BASE_DIR, "debug_login.png"), full_page=True)
            with open(os.path.join(BASE_DIR, "debug_login.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"  Сохранил debug_login.png и debug_login.html (адрес: {page.url})")
        except Exception:
            pass
        return False


def login():
    """Войти: авто-вход по .env или ручной ввод."""
    if not LESSON_IDS:                                                      # ← ИЗМЕНЕНО (защита)
        sys.exit("Не заданы уроки. Заполни GC_LESSON_IDS в .env (id через запятую).")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_page()

        if GC_LOGIN and GC_PASSWORD:
            print(f">>> Обнаружены креды в .env: {GC_LOGIN}")
            print(">>> Пытаюсь выполнить авто-вход...")
            if auto_login(page):
                page.goto(URL_TEMPLATE.format(lesson_id=LESSON_IDS[0]),     # ← ИЗМЕНЕНО
                          wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                if not re.search(r"(login|sign|enter|auth)", page.url, re.I):
                    print(">>> Авто-вход успешен, профиль сохранён")
                    browser.close()
                    print(f"Профиль сохранён в {USER_DATA_DIR}")
                    return
                print(">>> Авто-вход, похоже, не сработал — переключаюсь на ручной ввод...")
            else:
                print(">>> Авто-вход не удался — переключаюсь на ручной ввод...")

        page.goto(URL_TEMPLATE.format(lesson_id=LESSON_IDS[0]))             # ← ИЗМЕНЕНО
        print(">>> Введи логин и пароль в открывшемся окне браузера.")
        print(">>> Ничего не нажимай в терминале: скрипт заметит вход сам.")
        try:
            page.wait_for_selector("input[type='password']", state="detached", timeout=600000)
        except Exception:
            print("За 10 минут вход не произошёл...")
        page.wait_for_timeout(3000)
        browser.close()
    print(f"Профиль сохранён в {USER_DATA_DIR}")


def expand_list(page):
    """Кликаем кнопку с id=showMoreAnswers, пока она не исчезнет."""
    clicks = 0
    while clicks < MAX_CLICKS:
        btn = page.locator("#showMoreAnswers")
        if btn.count() == 0:
            page.wait_for_timeout(3000)
            if btn.count() == 0:
                break
        if not btn.is_visible():
            break
        label = " ".join(btn.inner_text().split())
        if re.search(r"еще\s+0\s+ответов", label):
            break
        m = re.search(r"(\d+)\s+из\s+(\d+)", label)
        if m:
            print(f"  -> подгружаю {m.group(1)} ответов, в очереди осталось: {m.group(2)}")
        before = page.locator("div.answer-content").count()
        btn.click()
        clicks += 1
        try:
            page.wait_for_function(
                "n => document.querySelectorAll('div.answer-content').length > n",
                arg=before, timeout=30000)
        except Exception:
            print("  ! Внимание: после клика новых карточек нет, останавливаюсь")
            break
    print(f"  кликов «Показать еще»: {clicks}")
    return clicks


def collect_htmls(page):
    """Слепки всех подстрок урока: раскрываем «Показать еще» до конца."""
    htmls = []
    expand_list(page)
    htmls.append(page.content())
    return htmls


def classify_comment(text):
    """Тип последнего комментария участницы."""
    if not text:
        return ""
    if "?" in text:
        return "вопрос"
    if THANKS_WORDS_RE.search(text):
        return "благодарность"
    if len(text.strip()) <= 15 and THANKS_EMOJI_RE.search(text):
        return "благодарность"
    return "комментарий"


def parse_cards(htmls, lesson_title, lesson_id, seen_answers):
    """Разбирает слепки одного урока, фильтрует по критериям, читает комментарии."""
    rows = []
    marker_counter = Counter()
    stats = {"cards": 0, "with_marker": 0, "matched": 0}

    for html in htmls:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("div.answer-content"):
            da = card.select_one(".answer-date a[href*='showAnswer']")
            answer_url = da.get("href", "") if da else ""
            m_id = re.search(r"showAnswer=(\d+)", answer_url)
            answer_id = m_id.group(1) if m_id else ""
            if answer_id and answer_id in seen_answers:
                continue
            if answer_id:
                seen_answers.add(answer_id)

            stats["cards"] += 1
            a = card.select_one(".title a.user-profile-link")
            name = a.get_text(strip=True) if a else ""
            user_id = a.get("data-user-id", "") if a else ""

            marker = card.select_one(".title > img[title]")
            title = marker.get("title", "") if marker else ""
            src = marker.get("src", "") if marker else ""
            if marker:
                stats["with_marker"] += 1
                marker_counter[title] += 1

            st = card.select_one(".answer-status-label")
            status = st.get_text(strip=True) if st else ""
            date_full = da.get("title", "") if da else ""

            comments = []
            for c in card.select(".comments .comment"):
                ca = c.select_one(".title a.user-profile-link")
                author = ca.get_text(strip=True) if ca else ""
                ct = (c.select_one(".text .emoji-text.text-content")
                      or c.select_one(".text .main .emoji-text")
                      or c.select_one(".text"))
                ctext = ct.get_text(" ", strip=True) if ct else ""
                ctime_el = c.select_one(".comment-time .value")
                ctime = ctime_el.get_text(strip=True) if ctime_el else ""
                comments.append((author, ctext, ctime))

            curators = [a for a, _, _ in comments if CURATOR_RE.search(a)]

            tail = []
            for a_, t_, tm_ in reversed(comments):
                if a_ and not CURATOR_RE.search(a_):
                    tail.append((t_, tm_))
                else:
                    break
            tail.reverse()
            last_from_participant = bool(tail)
            combined_text = "\n".join(t for t, _ in tail)
            first_tail_time = tail[0][1] if tail else ""
            comment_kind = classify_comment(combined_text) if tail else ""

            if CURATOR and CURATOR not in title:
                continue
            if TRAINING and TRAINING not in title:
                continue
            if MARKER_SRC and MARKER_SRC not in src:
                continue
            stats["matched"] += 1

            if curators and not any(CURATOR.lower() in c.lower() for c in curators):
                print(f"  ! Внимание: {name} — маркер куратора, но комментарий от: {curators}")  # ← ИЗМЕНЕНО

            reasons = []
            if not curators:
                reasons.append("нет ответа на ответ")
            if last_from_participant and comment_kind in ("вопрос", "комментарий"):
                reasons.append("вопрос/сообщение в комментарии")

            rows.append({
                "урок_id": lesson_id,
                "урок": lesson_title,
                "участник": name,
                "id участника": user_id,
                "маркер (title)": title,
                "статус": status,
                "дата ответа": date_full,
                "ссылка на ответ": answer_url,
                "комментарий куратора": ", ".join(curators) if curators else "нет",
                "ждёт ответа на комментарий": "да" if last_from_participant else "нет",
                "тип комментария": comment_kind,
                "текст комментария": combined_text,
                "время комментария": first_tail_time,
                "почему ждёт": "; ".join(reasons),
            })
    return rows, stats, marker_counter


def _parse_date(r):
    """Возвращает datetime из поля 'дата ответа' или None."""
    raw = r.get("дата ответа", "").replace("Время обновления ответа ", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def generate_html(rows, out_path):
    """HTML-отчёт: светофор, вопросы/благодарности в комментариях, поиск, сортировка."""
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    waiting = [r for r in rows if r.get("комментарий куратора") == "нет"]
    comment_waiting = [r for r in rows if r.get("ждёт ответа на комментарий") == "да"]
    active_waiting = [r for r in comment_waiting if r.get("тип комментария") != "благодарность"]
    thanks_waiting = [r for r in comment_waiting if r.get("тип комментария") == "благодарность"]
    has_lessons = any(r.get("урок") for r in rows)

    css = """
    body { font-family: 'Segoe UI', Arial, sans-serif; margin: 24px; color: #222; background: #fafafa; }
    h1 { font-size: 22px; }
    h2 { font-size: 18px; margin-top: 28px; border-bottom: 2px solid #e8a87c; padding-bottom: 6px; }
    .meta { color: #666; font-size: 13px; margin: 8px 0 20px; }
    .toolbar { display: flex; gap: 10px; align-items: center; margin: 10px 0 14px; flex-wrap: wrap; }
    .toolbar input[type="text"] { padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; min-width: 220px; }
    .toolbar button { padding: 7px 14px; border: 1px solid #1a73e8; background: #1a73e8; color: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
    .toolbar button.secondary { background: #fff; color: #1a73e8; }
    table { border-collapse: collapse; width: 100%; margin-top: 10px; background: #fff; }
    th, td { border: 1px solid #ddd; padding: 8px 10px; text-align: left; font-size: 14px; vertical-align: top; }
    th { background: #f5f5f5; cursor: pointer; user-select: none; }
    th:hover { background: #ececec; }
    th .arrow { margin-left: 6px; font-size: 11px; }
    th.sorted-asc .arrow::after { content: "▲"; color: #1a73e8; }
    th.sorted-desc .arrow::after { content: "▼"; color: #1a73e8; }
    tr.no-reply { background: #fff3f3; }
    tr.age-red { background: #ffe0e0 !important; }
    tr.age-red td { color: #a00000; font-weight: 500; }
    tr.age-yellow { background: #fff6d6 !important; }
    tr.age-yellow td { color: #7a5a00; }
    tr.age-green { background: #e6f5e6 !important; }
    tr.age-green td { color: #1d6b1d; }
    tr.wait-comment { background: #fff8e1 !important; }
    tr.wait-comment td { color: #7a5a00; }
    tr.thanks { background: #f0f0f0 !important; }
    tr.thanks td { color: #888; }
    tr.hidden { display: none; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; }
    .badge.wait { background: #ffd6d6; color: #a00000; }
    .badge.mid { background: #fff6d6; color: #7a5a00; }
    .badge.ok { background: #d6f5d6; color: #007700; }
    .badge.gray { background: #e8e8e8; color: #666; }
    .countdown { font-size: 12px; color: #666; margin-left: 6px; }
    .comment-cell { max-width: 700px; white-space: pre-line; }
    details { margin-top: 4px; }
    summary { cursor: pointer; color: #1a73e8; font-size: 12px; }
    a { color: #1a73e8; }
    .sort-key { display: none; }
    """

    js = r"""
<script>
document.querySelectorAll('table').forEach(table => {
  const headers = table.querySelectorAll('th');
  const tbody = table.querySelector('tbody') || table;
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  headers.forEach((th, colIdx) => {
    th.innerHTML += '<span class="arrow"></span>';
    th.addEventListener('click', () => {
      const asc = !th.classList.contains('sorted-asc');
      headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
      th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
      rows.sort((a, b) => {
        const aKey = (a.cells[colIdx].querySelector('.sort-key') || {}).textContent || a.cells[colIdx].textContent;
        const bKey = (b.cells[colIdx].querySelector('.sort-key') || {}).textContent || b.cells[colIdx].textContent;
        if (/^\d{4}-\d{2}-\d{2}/.test(aKey) && /^\d{4}-\d{2}-\d{2}/.test(bKey)) {
          return asc ? aKey.localeCompare(bKey) : bKey.localeCompare(aKey);
        }
        return asc ? aKey.localeCompare(bKey, 'ru') : bKey.localeCompare(aKey, 'ru');
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
});

function filterWaiting() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  document.querySelectorAll('#waiting-table tr.no-reply').forEach(tr => {
    const name = tr.cells[0].textContent.toLowerCase();
    tr.classList.toggle('hidden', q && !name.includes(q));
  });
}

function toggleWaitingSort() {
  const th = document.querySelector('#waiting-table th[data-role="date"]');
  if (!th) return;
  th.click();
  const btn = document.getElementById('waiting-sort-btn');
  const cur = th.classList.contains('sorted-asc') ? 'asc' : 'desc';
  btn.textContent = (cur === 'asc')
    ? '🔃 Сортировать: новые сверху'
    : '🔃 Сортировать: старые сверху';
}

window.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('waiting-sort-btn');
  if (btn) btn.click();
});
</script>
"""

    def clean_date(r):
        return r.get("дата ответа", "").replace("Время обновления ответа ", "")

    def date_cell(r):
        d = clean_date(r)
        return f'<td><span class="sort-key">{escape(d)}</span>{escape(d)}</td>'

    def link(r):
        href = escape(r.get("ссылка на ответ", ""), quote=True)
        return f'<a href="{href}" target="_blank">открыть ответ</a>'

    def comment_cell(text):
        if len(text) > 250:
            return (f'<td class="comment-cell">{escape(text[:250])}… '
                    f'<details><summary>читать полностью</summary>'
                    f'{escape(text)}</details></td>')
        return f'<td class="comment-cell">{escape(text)}</td>'

    def kind_badge(kind):
        if kind == "вопрос":
            return '<span class="badge wait">❓ вопрос</span>'
        if kind == "благодарность":
            return '<span class="badge gray">🙏 спасибо</span>'
        if kind == "комментарий":
            return '<span class="badge mid">💬 сообщение</span>'
        return '<span class="badge mid">💬 ждёт</span>'

    cnt = {"red": 0, "yellow": 0, "green": 0}
    now = datetime.now()
    for r in waiting:
        dt = _parse_date(r)
        if not dt:
            continue
        hours = (now - dt).total_seconds() / 3600
        if hours >= 24:
            cnt["red"] += 1
        elif hours >= 12:
            cnt["yellow"] += 1
        else:
            cnt["green"] += 1

    parts = [
        "<!DOCTYPE html>",
        '<html lang="ru">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>Участницы куратора {escape(CURATOR)}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        f"<h1>🍁 Участницы куратора {escape(CURATOR)}</h1>",
        f'<div class="meta">Отчёт создан: {now_str} · Всего участниц: {len(rows)} · '
        f'Ждут ответа: {len(waiting)} · '
        f'<span style="color:#a00000">🔴 &gt;24ч: {cnt["red"]}</span> · '
        f'<span style="color:#7a5a00">🟡 12–24ч: {cnt["yellow"]}</span> · '
        f'<span style="color:#1d6b1d">🟢 &lt;12ч: {cnt["green"]}</span> · '
        f'<span style="color:#7a5a00">📨 ждут ответа в комментариях: {len(active_waiting)}</span> · '
        f'<span style="color:#888">🙏 благодарностей: {len(thanks_waiting)}</span></div>',
    ]

    if waiting:
        parts.append("<h2>⏳ Ждут ответа куратора</h2>")
        parts.append('<div class="toolbar">')
        parts.append('<input type="text" id="search-input" placeholder="🔍 Поиск по имени..." oninput="filterWaiting()">')
        parts.append('<button id="waiting-sort-btn" onclick="toggleWaitingSort()">🔃 Сортировать: новые сверху</button>')
        parts.append('<button class="secondary" onclick="document.getElementById(\'search-input\').value=\'\'; filterWaiting();">Сбросить поиск</button>')
        parts.append('</div>')

        head = '<table id="waiting-table"><thead><tr><th>Участница</th>'
        head += "<th>Урок</th>" if has_lessons else ""
        head += '<th data-role="date">Дата ответа</th><th>Ссылка</th></tr></thead><tbody>'
        parts.append(head)

        for r in waiting:
            dt = _parse_date(r)
            if dt:
                hours = (now - dt).total_seconds() / 3600
                if hours >= 24:
                    css_class, emoji = "no-reply age-red", "🔴"
                    days, h = divmod(int(hours), 24)
                    countdown = f'<span class="countdown">({days}д {h}ч)</span>' if days else f'<span class="countdown">({int(hours)}ч)</span>'
                elif hours >= 12:
                    css_class, emoji = "no-reply age-yellow", "🟡"
                    countdown = f'<span class="countdown">({int(hours)}ч)</span>'
                else:
                    css_class, emoji = "no-reply age-green", "🟢"
                    countdown = f'<span class="countdown">({int(hours)}ч)</span>'
            else:
                css_class, emoji, countdown = "no-reply", "", ""

            row_html = f'<tr class="{css_class}"><td>{emoji} {escape(r.get("участник", ""))}{countdown}</td>'
            if has_lessons:
                row_html += f'<td>{escape(r.get("урок", ""))}</td>'
            row_html += f'{date_cell(r)}<td>{link(r)}</td></tr>'
            parts.append(row_html)
        parts.append("</tbody></table>")

    if active_waiting:
        parts.append("<h2>📨 Вопросы и сообщения в комментариях (нужен ответ)</h2>")
        head = '<table id="comment-table"><thead><tr><th>Участница</th>'
        head += "<th>Урок</th>" if has_lessons else ""
        head += '<th>Время</th><th>Сообщение участницы</th><th>Ссылка</th></tr></thead><tbody>'
        parts.append(head)
        for r in active_waiting:
            text = r.get("текст комментария", "")
            q = "❓" if r.get("тип комментария") == "вопрос" else "💬"
            row_html = f'<tr class="wait-comment"><td>{q} {escape(r.get("участник", ""))}</td>'
            if has_lessons:
                row_html += f'<td>{escape(r.get("урок", ""))}</td>'
            row_html += f'<td>{escape(r.get("время комментария", ""))}</td>'
            row_html += comment_cell(text)
            row_html += f'<td>{link(r)}</td></tr>'
            parts.append(row_html)
        parts.append("</tbody></table>")

    if thanks_waiting:
        parts.append("<h2>🙏 Благодарности (ответ не обязателен)</h2>")
        head = '<table><thead><tr><th>Участница</th>'
        head += "<th>Урок</th>" if has_lessons else ""
        head += '<th>Время</th><th>Сообщение участницы</th><th>Ссылка</th></tr></thead><tbody>'
        parts.append(head)
        for r in thanks_waiting:
            text = r.get("текст комментария", "")
            row_html = f'<tr class="thanks"><td>🙏 {escape(r.get("участник", ""))}</td>'
            if has_lessons:
                row_html += f'<td>{escape(r.get("урок", ""))}</td>'
            row_html += f'<td>{escape(r.get("время комментария", ""))}</td>'
            row_html += comment_cell(text)
            row_html += f'<td>{link(r)}</td></tr>'
            parts.append(row_html)
        parts.append("</tbody></table>")

    groups = {}
    for r in rows:
        groups.setdefault(r.get("урок") or "Список участниц", []).append(r)
    for lesson, items in groups.items():
        parts.append(f"<h2>📚 {escape(lesson)} ({len(items)})</h2>")
        parts.append('<table><thead><tr><th>Участница</th><th>Статус</th>'
                     '<th data-role="date">Дата ответа</th><th>Ответ куратора</th>'
                     '<th>Комментарий участницы</th><th>Ссылка</th></tr></thead><tbody>')
        for r in items:
            has = r.get("комментарий куратора") != "нет"
            badge = '<span class="badge ok">есть</span>' if has else '<span class="badge wait">нет</span>'
            if r.get("ждёт ответа на комментарий") == "да":
                wc_badge = kind_badge(r.get("тип комментария", ""))
            else:
                wc_badge = "—"
            parts.append(
                "<tr>"
                f'<td>{escape(r.get("участник", ""))}</td>'
                f'<td>{escape(r.get("статус", ""))}</td>'
                f'{date_cell(r)}'
                f"<td>{badge}</td>"
                f"<td>{wc_badge}</td>"
                f"<td>{link(r)}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    parts.append(js)
    parts.append("</body></html>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print(f"HTML-отчёт сохранён: {out_path}")


def run():
    if not os.path.exists(USER_DATA_DIR) and not (GC_LOGIN and GC_PASSWORD):
        sys.exit(
            f"Нет папки {USER_DATA_DIR} и не заданы креды в .env.\n"
            f"Сначала выполни: python parse_answers.py login\n"
            f"Или добавь GC_LOGIN и GC_PASSWORD в .env — тогда скрипт войдёт сам."
        )
    if not LESSON_IDS:                                                      # ← ИЗМЕНЕНО (защита)
        sys.exit("Не заданы уроки. Заполни GC_LESSON_IDS в .env (id через запятую).")

    all_rows = []
    marker_counter = Counter()
    stats = {"cards": 0, "with_marker": 0, "matched": 0}
    seen_answers = set()
    last_html = ""

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_page()
        page.set_default_timeout(120000)

        for lesson_id in LESSON_IDS:
            url = URL_TEMPLATE.format(lesson_id=lesson_id)                  # ← ИЗМЕНЕНО
            print(f"\n=== Урок id={lesson_id}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(2000)

            if re.search(r"(login|sign|enter|auth)", page.url, re.I):
                if GC_LOGIN and GC_PASSWORD:
                    print("  ! Сессия протухла, выполняю авто-вход...")
                    if auto_login(page):
                        page.goto(url, wait_until="domcontentloaded", timeout=120000)
                        page.wait_for_timeout(2000)
                        if re.search(r"(login|sign|enter|auth)", page.url, re.I):
                            browser.close()
                            sys.exit("Авто-вход выполнен, но GetCourse снова просит логин.\n"
                                     "Возможно, включена капча или 2FA — проверь креды в .env.")
                    else:
                        browser.close()
                        sys.exit("Сессия протухла, авто-вход не удался.\n"
                                 "Проверь GC_LOGIN/GC_PASSWORD в .env или запусти: python parse_answers.py login")
                else:
                    browser.close()
                    sys.exit("Сессия протухла. Добавь GC_LOGIN и GC_PASSWORD в .env,\n"
                             "или запусти: python parse_answers.py login")

            try:
                page.wait_for_selector("div.answer-content", timeout=20000)
            except Exception:
                print("  На этом уроке ответов нет — пропускаю")
                continue

            htmls = collect_htmls(page)
            last_html = htmls[-1]
            lesson_title = page.title() or str(lesson_id)
            print(f"  слепков: {len(htmls)}, урок: {lesson_title}")

            rows, lst_stats, lst_counter = parse_cards(htmls, lesson_title, lesson_id, seen_answers)
            all_rows.extend(rows)
            for k, v in lst_stats.items():
                stats[k] += v
            marker_counter.update(lst_counter)

        browser.close()

    if last_html:
        with open(DEBUG_HTML, "w", encoding="utf-8") as f:
            f.write(last_html)

    cols = ["урок_id", "урок", "участник", "id участника", "маркер (title)", "статус",
            "дата ответа", "ссылка на ответ", "комментарий куратора",
            "ждёт ответа на комментарий", "тип комментария", "текст комментария",
            "время комментария", "почему ждёт"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)

    waiting_rows = [r for r in all_rows
                    if r.get("комментарий куратора") == "нет"
                    or r.get("тип комментария") in ("вопрос", "комментарий")]
    with open(OUT_WAITING, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(waiting_rows)

    generate_html(all_rows, OUT_HTML)

    comment_waiting = [r for r in all_rows if r.get("ждёт ответа на комментарий") == "да"]
    thanks_cnt = sum(1 for r in comment_waiting if r.get("тип комментария") == "благодарность")
    print(f"\n=== ИТОГО ПО ВСЕМ УРОКАМ ===")
    print(f"Уникальных карточек: {stats['cards']}")
    print(f"Из них с маркером: {stats['with_marker']}")
    print(f"Прошли по обоим критериям: {stats['matched']}")
    print(f"Из них ждут ответа: {len(waiting_rows)} -> {OUT_WAITING}")
    print(f"Последний комментарий без ответа: {len(comment_waiting)} "
          f"(вопросов/сообщений: {len(comment_waiting) - thanks_cnt}, "
          f"благодарностей: {thanks_cnt})")
    print("Распределение маркеров:")
    for t, n in marker_counter.most_common(10):
        print(f"  {n:3d}  {t}")
    print(f"Результат: {OUT_CSV}")
    print(f"HTML-отчёт: {OUT_HTML}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    login() if mode == "login" else run()