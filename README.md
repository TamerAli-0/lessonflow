# LessonFlow

LessonFlow turns your own teaching material into a finished lesson plan.

You give it a PDF or a Word file. It reads the material, suggests the topics
worth teaching, builds a week-by-week plan around them, and lets you edit every
line before it writes the result into a Word lesson-plan template you can print
or hand in.

It runs on your own computer. Nothing is uploaded anywhere except the text you
choose to send to the writing service you pick.

---

## Install on Windows

Open **Command Prompt** (press Start, type `cmd`, press Enter) and paste this
single line:

```
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm https://raw.githubusercontent.com/TamerAli-0/lessonflow/main/install.ps1 | iex"
```

That is the whole setup. The installer:

- checks what the computer already has and shows you a short list,
- installs Python only if it is missing or too old, and asks first,
- leaves a working Python alone instead of reinstalling it,
- reuses a copy already sitting in your Downloads folder if it finds one,
- puts a **LessonFlow** icon on the Desktop and in the Start menu.

You do not need administrator rights, and nothing is installed without a yes.

To update later, paste the same line again. It refreshes the program and keeps
everything you have already saved.

---

## Using it

1. Double-click the **LessonFlow** icon. A window opens and the page follows a
   few seconds later at `http://127.0.0.1:5050`.
2. Paste an API key the first time. See below for where to get a free one.
3. Upload your teaching material as PDF, `.docx`, or `.doc`. For a PDF you can
   give a page range instead of the whole file.
4. Pick the topics you want. LessonFlow preselects the ones it recommends and
   offers the rest in an expandable list, each with the exact page it came from.
   Drag them into the order you want to teach.
5. Set the pacing: how many weeks, minutes per lesson, lessons per week, the
   theory and practical balance, the start date, and the class level.
6. Edit anything. Every paragraph has its own editor, with delete, add, bold,
   italic, and text size. The ✦ button offers five rewrites of a line, or you
   can type an instruction in plain language and get five choices back.
7. Download `generated-lesson-plan.docx` and open it in Word.

The preview on the review screen is the real Word layout, page breaks and all,
so what you see is what prints.

---

## Getting a free API key

LessonFlow does not include a writing service. You supply a key for one of
these, and free tiers are enough to work with:

| Service | Where to get a key |
| --- | --- |
| Google Gemini | [aistudio.google.com](https://aistudio.google.com/apikey) |
| Groq | [console.groq.com](https://console.groq.com/keys) |
| Mistral | [console.mistral.ai](https://console.mistral.ai/api-keys) |
| OpenRouter | [openrouter.ai](https://openrouter.ai/keys) |

For Gemini you just choose Recommended, Best quality, Fastest, or Older stable
from a dropdown. There is no model name to type.

The key is used for the request you are making and is never written to disk.

---

## Things worth knowing

- Keep the black window open while you work. Closing it stops the program.
- Scanned PDFs with no selectable text will not work. LessonFlow tells you
  instead of inventing content.
- Page ranges are for PDFs only. Word files are read in full, because Word page
  numbers move around depending on fonts and printer settings.
- The finished plan is exactly two pages, the size of the template. If your
  edits push it past two pages, the download waits until it fits again.
- Free services may keep what you send them, so do not upload anything with
  student personal information in it.
- Your uploads and finished plans stay in the `runtime` folder inside the
  install directory. Updates never touch it.

---

## If something goes wrong

**The command does nothing, or says running scripts is disabled.**
Use the exact line above. The `-ExecutionPolicy Bypass` part is what allows it.

**It says Python is installed but this window cannot see it.**
Close the Command Prompt, open a new one, and paste the line again. Windows
only gives new programs to windows opened after the install.

**The icon does nothing.**
Open the install folder, `%LOCALAPPDATA%\LessonFlow`, and run
`Start LessonFlow.bat` directly. The error will stay on screen.

**The page will not load.**
Give it a few more seconds, then go to `http://127.0.0.1:5050` yourself.

---

## Uninstall

Delete the `LessonFlow` folder in `%LOCALAPPDATA%` and the two shortcuts. That
is everything. Python stays, since other programs may be using it.

---

## Running from source

For macOS, Linux, or development work:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5050`.

Requires Python 3.10 or newer. The Word template ships in
`assets/lesson-plan-template.docx` and is picked up automatically. To use a
different one, set `LESSON_PLANNER_TEMPLATE` to its path or choose it in the
export card. A different template still needs recognisable section labels such
as `WARM UP`, `GUIDED INSTRUCTION`, and `PROGRESS CHECK`.

Run the tests with:

```bash
pip install pytest
python -m pytest
```
