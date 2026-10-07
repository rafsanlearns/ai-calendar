# Personal AI Agent (self-spawning assistants)

একটি ব্যক্তিগত AI agent। মূল এজেন্ট কাজ দেখে সিদ্ধান্ত নেয়: সহজ হলে নিজে করে, জটিল হলে
প্রয়োজনমতো বিশেষজ্ঞ সহকারী (researcher, coder, writer, reviewer ইত্যাদি) বানিয়ে কাজ ভাগ করে দেয়,
একাধিক সহকারী সমান্তরালে চলে, শেষে সবার ফলাফল মিলিয়ে একটি উত্তর দেয়।

## চালানোর নিয়ম

```bash
git clone <আপনার-repo-url>
cd <repo-folder>

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
# .env খুলে ANTHROPIC_API_KEY বসান

python agent.py
```

## উদাহরণ প্রম্পট

- `ঢাকা, দিল্লি ও টোকিওর বর্তমান জনসংখ্যা নিয়ে তিনজন আলাদা সহকারী দিয়ে খোঁজ নাও, তারপর তুলনা করে comparison.md লেখো`
- `একটা Python স্ক্রিপ্ট লেখো যেটা CSV থেকে গড় বের করে, চালিয়ে টেস্ট করো, আর আরেকজন দিয়ে রিভিউ করাও`

## নিরাপত্তা

- `write_file` ও `run_python` চালানোর আগে প্রতিবার আপনার `y` অনুমোদন লাগে
- ফাইল পড়া/লেখা শুধু `workspace/` ফোল্ডারে সীমাবদ্ধ
- `run_python` ৩০ সেকেন্ডের টাইমআউটে চলে, তবে এটা sandbox নয়: অনুমোদন দেওয়ার আগে কোড পড়ে নিন
- সহকারীরা নতুন সহকারী বানাতে পারে না, আর প্রতি অনুরোধে সর্বোচ্চ `MAX_ASSISTANTS` টি সহকারী বানানো যায়
- **API key কখনো কমিট করবেন না।** `.env` আগে থেকেই `.gitignore`-এ আছে

## খরচ নিয়ে সতর্কতা

প্রতিটি সহকারী আলাদা API কল করে, তাই জটিল কাজে খরচ বাড়ে। প্রয়োজনে `.env`-এ
`MAX_ASSISTANTS` ও `MAX_STEPS` কমিয়ে দিন, আর Anthropic Console-এ মাসিক খরচের সীমা ঠিক করে রাখুন।

## নতুন টুল যোগ করা

1. `agent.py`-তে একটা ফাংশন লিখুন
2. `BASE_FUNCS`-এ নাম যোগ করুন
3. `BASE_TOOLS`-এ বিবরণ ও ইনপুট স্কিমা দিন
4. ঝুঁকিপূর্ণ হলে নামটা `NEEDS_APPROVAL`-এ রাখুন

## GitHub-এ আপলোড

```bash
git init
git add .
git status          # নিশ্চিত হোন .env তালিকায় নেই
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<ইউজারনেম>/<repo>.git
git push -u origin main
```

Repo **private** রাখাই ভালো।
