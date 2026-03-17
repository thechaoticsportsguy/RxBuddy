# 💊 RxBuddy — AI-Powered Pharmacy Consultation App

> A full-stack AI web application that allows patients to search 500+ common pharmacy questions — covering drug interactions, OTC medications, dosage guidance, and more — powered by semantic search, voice recognition, and the Claude AI API.

---

## 🧠 What It Does

Patients visiting a pharmacy often have simple but important questions:
- *"Can I take ibuprofen with my blood pressure medication?"*
- *"What cold medicine is safe if I have diabetes?"*
- *"How much Tylenol can I take per day?"*

RxBuddy lets patients get instant, AI-powered answers without waiting for a pharmacist consultation — available 24/7 on any device. Just type or speak your question and RxBuddy handles the rest.

---

## 🏗️ Project Structure

```
rxbuddy/
│
├── backend/                  # FastAPI Python backend
│   ├── main.py               # API entry point
│   ├── search.py             # ML search engine (TF-IDF + KNN)
│   ├── database.py           # PostgreSQL connection & queries
│   ├── claude_client.py      # Anthropic Claude API integration
│   └── models.py             # Data models / schemas
│
├── data/                     # Data pipeline
│   ├── questions.csv         # 500+ pharmacy questions dataset
│   ├── load_data.py          # Pandas pipeline to clean & load data
│   └── seed_db.py            # Seeds PostgreSQL database
│
├── ml/                       # Machine Learning
│   ├── tfidf_search.py       # TF-IDF + Cosine Similarity baseline
│   ├── knn_search.py         # KNN + Sentence Embeddings
│   └── train.py              # Model training & retraining pipeline
│
├── frontend/                 # React / Next.js frontend
│   ├── pages/
│   │   ├── index.js          # Home / search page
│   │   ├── results.js        # Search results page
│   │   └── category.js       # Browse by category page
│   ├── components/
│   │   ├── SearchBar.js      # Includes voice recognition button
│   │   ├── QuestionCard.js
│   │   └── AnswerModal.js
│   └── styles/
│
├── dashboard/                # Streamlit analytics dashboard
│   └── app.py                # Most searched queries, usage trends
│
├── .env.example              # Environment variables template
├── requirements.txt          # Python dependencies
├── package.json              # Node dependencies
└── README.md
```

---

## 🛠️ Tech Stack

### Languages
| Language | Usage |
|----------|-------|
| Python | Backend, ML pipeline, data processing |
| JavaScript | React frontend |
| SQL | PostgreSQL queries, search logging |

### Machine Learning & Data
| Library | Usage |
|---------|-------|
| scikit-learn | TF-IDF vectorizer, KNN model, cosine similarity |
| pandas | Data cleaning, structuring 500 questions dataset |
| NumPy | Numerical operations, vector math |
| sentence-transformers | Sentence embeddings for semantic search |
| NLTK | Drug name extraction, NLP preprocessing |

### Backend
| Tool | Usage |
|------|-------|
| FastAPI | REST API server |
| PostgreSQL | Questions database + search logs |
| SQLAlchemy | ORM for database interaction |
| Anthropic Claude API | AI-generated answers |

### Frontend
| Tool | Usage |
|------|-------|
| React / Next.js | Patient-facing web interface |
| Tailwind CSS | Styling |
| Web Speech API | Voice recognition for hands-free search |

### Analytics
| Tool | Usage |
|------|-------|
| Streamlit | Internal analytics dashboard |
| Matplotlib / Seaborn | Search trend visualizations |

### Project Management
| Tool | Usage |
|------|-------|
| JIRA | Sprint planning, ticket tracking, team collaboration |
| GitHub | Version control, pull requests, code review |

### DevOps
| Tool | Usage |
|------|-------|
| Vercel | Frontend deployment |
| Railway | Backend + PostgreSQL hosting |

---

## 🤖 ML Architecture

```
User types or speaks a query
            ↓
   Web Speech API (voice → text)
            ↓
TF-IDF + Cosine Similarity  ←── Instant baseline match
            ↓
KNN + Sentence Embeddings   ←── Semantic understanding
            ↓
Popularity weighting        ←── From PostgreSQL search logs
            ↓
Claude API                  ←── Generates full AI answer
            ↓
User clicks result → logged → model improves over time
```

RxBuddy uses a **feedback-driven ML system** — every user search is logged to PostgreSQL, and the model continuously re-ranks results based on real patient behavior.

---

## 🗄️ Database Schema

```sql
-- Questions table
CREATE TABLE questions (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    category VARCHAR(50),
    tags TEXT[],
    answer TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Search logs table (feeds ML retraining)
CREATE TABLE search_logs (
    id SERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    matched_question_id INT REFERENCES questions(id),
    clicked BOOLEAN DEFAULT FALSE,
    session_id VARCHAR(100),
    searched_at TIMESTAMP DEFAULT NOW()
);
```

---

## 📊 Analytics Dashboard

The Streamlit dashboard (internal use) shows:
- Top 20 most searched questions
- Search volume by category
- Click-through rate per question
- Daily/weekly usage trends
- Unanswered query clustering (questions users ask that don't match anything)

---

## 🚀 Getting Started

### 1. Clone the repo
```bash
git clone https://github.com/thechaoticsportsguy/rxbuddy.git
cd rxbuddy
```

### 2. Set up Python environment
```bash
pip install -r requirements.txt
```

### 3. Set up environment variables
```bash
cp .env.example .env
# Add your Anthropic API key and PostgreSQL connection string
```

### 4. Seed the database
```bash
python data/seed_db.py
```

### 5. Run the backend
```bash
uvicorn backend.main:app --reload
```

### 6. Run the frontend
```bash
cd frontend
npm install
npm run dev
```

### 7. Run the analytics dashboard
```bash
streamlit run dashboard/app.py
```

---

## 📋 Resume Bullets

> *Built RxBuddy, a full-stack AI pharmacy consultation app using FastAPI, React, PostgreSQL, and the Claude API — implementing a hybrid NLP search engine combining TF-IDF, KNN, and sentence embeddings with a PostgreSQL feedback loop that continuously improves from real user search behavior, with voice recognition via Web Speech API, deployed on Vercel + Railway.*

---

## ⚠️ Disclaimer

RxBuddy is an informational tool only. It does not replace professional medical or pharmaceutical advice. Always consult a licensed pharmacist or physician for medical decisions.

---

## 👥 Authors

**Om Gohel**
- GitHub: [@thechaoticsportsguy](https://github.com/thechaoticsportsguy)
- LinkedIn: [linkedin.com/in/omgohel](https://linkedin.com/in/omgohel)

**Mihir Jani**
- GitHub: [@mihirzx](https://github.com/mihirzx)
- LinkedIn: [linkedin.com/in/mihir-jani](https://linkedin.com/in/mihir-jani)
- Email: mihirjani@umass.edu
