"""
src/setup.py – Interactive setup wizard for openjob.

Run: openjob setup
"""
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# ── ANSI colors ───────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
WHITE  = "\033[97m"

def c(text, color): return f"{color}{text}{RESET}"
def bold(text):     return c(text, BOLD)
def ok(text):       print(f"  {c('✓', GREEN)} {text}")
def err(text):      print(f"  {c('✗', RED)} {text}")
def info(text):     print(f"  {c('→', CYAN)} {text}")
def warn(text):     print(f"  {c('!', YELLOW)} {text}")

# ── LLM provider catalogue ────────────────────────────────────────────────────

PROVIDERS = [
    {
        "id":       "openai",
        "label":    "OpenAI",
        "models":   ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "default":  "gpt-4o",
        "key_env":  "OPENAI_API_KEY",
        "key_hint": "sk-...",
        "url":      "https://platform.openai.com/api-keys",
        "badge":    "⭐ recommended",
    },
    {
        "id":       "anthropic",
        "label":    "Anthropic",
        "models":   ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
        "default":  "claude-3-5-sonnet-20241022",
        "key_env":  "ANTHROPIC_API_KEY",
        "key_hint": "sk-ant-...",
        "url":      "https://console.anthropic.com/settings/keys",
        "badge":    "",
    },
    {
        "id":       "gemini",
        "label":    "Google Gemini",
        "models":   ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"],
        "default":  "gemini-1.5-flash",
        "key_env":  "GEMINI_API_KEY",
        "key_hint": "AIza...",
        "url":      "https://aistudio.google.com/app/apikey",
        "badge":    "free tier available",
    },
    {
        "id":       "groq",
        "label":    "Groq",
        "models":   ["llama-3.1-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        "default":  "llama-3.1-70b-versatile",
        "key_env":  "GROQ_API_KEY",
        "key_hint": "gsk_...",
        "url":      "https://console.groq.com/keys",
        "badge":    "fast + free tier",
    },
    {
        "id":       "ollama",
        "label":    "Ollama (local)",
        "models":   ["llama3.1", "mistral", "gemma2", "qwen2.5"],
        "default":  "llama3.1",
        "key_env":  None,
        "key_hint": None,
        "url":      "https://ollama.com",
        "badge":    "no API key needed",
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_env() -> dict:
    env_path = PROJECT_ROOT / ".env"
    result = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip().strip('"')
    return result


def write_env(updates: dict) -> None:
    env_path = PROJECT_ROOT / ".env"
    existing = []
    seen = set()
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in updates:
                    existing.append(f'{k}={updates[k]}')
                    seen.add(k)
                    continue
            existing.append(line)
    for k, v in updates.items():
        if k not in seen:
            existing.append(f'{k}={v}')
    env_path.write_text("\n".join(existing).rstrip() + "\n")


def prompt(text, default="", secret=False) -> str:
    disp = f" [{c(default, DIM)}]" if default else ""
    try:
        if secret:
            val = getpass.getpass(f"  {text}{disp}: ")
        else:
            val = input(f"  {text}{disp}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)
    return val or default


def choose(items: list, prompt_text: str = "Choice") -> int:
    """Print numbered list, return 0-based index."""
    for i, item in enumerate(items, 1):
        print(f"  {c(str(i), CYAN)}. {item}")
    while True:
        try:
            raw = input(f"\n  {prompt_text} [1-{len(items)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                return idx
        except (ValueError, KeyboardInterrupt, EOFError):
            pass
        print(f"  {c('Please enter a number between 1 and ' + str(len(items)), YELLOW)}")


# ── Connection test ───────────────────────────────────────────────────────────

def test_openai(api_key: str, model: str, base_url: str | None = None) -> bool:
    import openai
    try:
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say 'ok' in one word."}],
            max_tokens=5,
            timeout=15,
        )
        return True
    except Exception as e:
        err(f"Connection failed: {e}")
        return False


def test_anthropic(api_key: str, model: str) -> bool:
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=model,
            max_tokens=5,
            messages=[{"role": "user", "content": "Say 'ok'."}],
        )
        return True
    except Exception as e:
        err(f"Connection failed: {e}")
        return False


# ── Setup sections ────────────────────────────────────────────────────────────

def setup_llm() -> dict:
    """Interactive LLM provider + model selection. Returns env var dict."""
    print(f"\n{bold('── LLM Provider ─────────────────────────────────────')}")
    print(f"  {c('Choose the AI model that will tailor your resumes:', WHITE)}\n")

    labels = []
    for p in PROVIDERS:
        badge = f"  {c('(' + p['badge'] + ')', DIM)}" if p["badge"] else ""
        labels.append(f"{bold(p['label'])}{badge}")
    idx = choose(labels, "Provider")
    provider = PROVIDERS[idx]

    print(f"\n  {c('Select model:', WHITE)}")
    model_idx = choose(provider["models"], "Model")
    model = provider["models"][model_idx]

    env_updates = {
        "LLM_MODE":    provider["id"],
        f"OPENAI_MODEL" if provider["id"] == "openai" else f"{provider['id'].upper()}_MODEL": model,
    }
    # Rename model key correctly
    env_updates = {"LLM_MODE": provider["id"]}
    env_updates[f"{provider['id'].upper()}_MODEL"] = model

    if provider["id"] == "ollama":
        print(f"\n  {c('Make sure Ollama is running: ', WHITE)}{c('ollama serve', DIM)}")
        ollama_url = prompt("Ollama base URL", "http://localhost:11434")
        env_updates["OLLAMA_BASE_URL"] = ollama_url
        ok(f"Ollama configured → {ollama_url}  model: {model}")
        return env_updates

    # API key entry
    print(f"\n  Get your API key: {c(provider['url'], CYAN)}")
    api_key = prompt(f"{provider['label']} API key", secret=True)
    if not api_key:
        warn("No API key entered — skipping connection test.")
        return env_updates

    env_updates[provider["key_env"]] = api_key

    # Test connection
    print(f"\n  {c('Testing connection…', DIM)}", end="", flush=True)
    success = False

    if provider["id"] == "anthropic":
        success = test_anthropic(api_key, model)
    elif provider["id"] == "gemini":
        success = test_openai(api_key, model, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    elif provider["id"] == "groq":
        success = test_openai(api_key, model, base_url="https://api.groq.com/openai/v1")
    else:
        success = test_openai(api_key, model)

    if success:
        print(f"\r  {c('✓ Connection successful!', GREEN)}  model: {c(model, CYAN)}")
    else:
        print(f"\r  {c('✗ Connection failed.', RED)} Double-check your API key and try again.")

    return env_updates


def setup_candidate() -> dict:
    """Collect candidate info. Returns env var dict."""
    print(f"\n{bold('── Candidate Info ────────────────────────────────────')}")
    print(f"  {c('This info appears in your cover letters and resumes.', DIM)}\n")
    existing = read_env()
    return {
        "CANDIDATE_NAME": prompt("Your full name",    existing.get("CANDIDATE_NAME", "")),
        "EMAIL":          prompt("Email address",     existing.get("EMAIL", "")),
        "LINKEDIN":       prompt("LinkedIn URL",      existing.get("LINKEDIN", "linkedin.com/in/yourusername")),
        "PORTFOLIO":      prompt("Portfolio / website", existing.get("PORTFOLIO", "")),
    }


def setup_resume_reminder():
    """Remind user to update their resume HTML files."""
    print(f"\n{bold('── Resume Templates ──────────────────────────────────')}")
    print(f"  Replace the placeholder content with your actual resume:\n")
    info(f"AI / ML roles   → {c('resume/base_resume_ai.html', CYAN)}")
    info(f"SDE / FS roles  → {c('resume/base_resume.html', CYAN)}")
    print(f"\n  {c('Keep the CSS — just update the content sections.', DIM)}")


def setup_discord():
    """Optional Discord webhook setup."""
    print(f"\n{bold('── Discord Notifications (optional) ─────────────────')}")
    want = prompt("Set up Discord notifications? [y/N]", "n").lower()
    if want != "y":
        return {}
    print(f"  {c('Create a webhook in Discord:', DIM)} Server Settings → Integrations → Webhooks")
    webhook = prompt("Discord webhook URL", "")
    return {"DISCORD_WEBHOOK": webhook} if webhook else {}


# ── Main entry point ──────────────────────────────────────────────────────────

def run_setup():
    print(f"\n{BOLD}{CYAN}{'═' * 52}{RESET}")
    print(f"{BOLD}{CYAN}  🤖  openjob setup{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 52}{RESET}")
    print(f"\n  Configure your job-search agent.\n")

    env_updates = {}

    # Step 1: LLM
    env_updates.update(setup_llm())

    # Step 2: Candidate info
    env_updates.update(setup_candidate())

    # Step 3: Discord (optional)
    env_updates.update(setup_discord())

    # Step 4: Write .env
    write_env(env_updates)

    # Step 5: Resume reminder
    setup_resume_reminder()

    # Done
    print(f"\n{BOLD}{GREEN}{'═' * 52}{RESET}")
    print(f"{BOLD}{GREEN}  ✅  Setup complete!{RESET}")
    print(f"{BOLD}{GREEN}{'═' * 52}{RESET}")
    print()
    info("Edit your resume HTML files (see above)")
    info(f"Run {c('openjob run', CYAN)} to start your first job search")
    info(f"Run {c('openjob retry <linkedin_url>', CYAN)} to test with one job")
    print()


if __name__ == "__main__":
    run_setup()
