# Personal WhatsApp AI Assistant

An AI-powered WhatsApp automation system built with FastAPI, WhatsApp Business Cloud API, OpenRouter, and a React-based Admin Dashboard.

The project combines an intelligent WhatsApp assistant with a functional web dashboard for managing conversations, contacts, AI behavior, approvals, settings, and system activity.

## Project Overview

This project consists of two main components:

### 1. Backend — WhatsApp AI Assistant

The backend handles WhatsApp communication, AI-powered message processing, conversation history, Smart Memory, security, and administration.

**Key features:**
- WhatsApp Business Cloud API integration
- OpenRouter AI integration with automatic fallback
- Automated message processing and replies
- Conversation History and Smart Memory systems
- Per-contact AI controls
- Working hours and human override
- Spam detection and rate limiting
- Sensitive-information approval workflow
- WhatsApp admin commands
- Admin API and system health monitoring

Detailed documentation: [Backend README](backend/README.md)

### 2. Frontend — Admin Dashboard

The frontend is a functional web-based dashboard connected to the FastAPI backend. Dashboard controls communicate with live backend API endpoints.

**Technology:** React, TypeScript, Vite, and Tailwind CSS.

**Dashboard features:**
- Overview dashboard
- Conversations and contacts management
- Approval management
- Global and per-contact AI controls
- Human override management
- Smart Memory management
- Working hours and settings
- System health monitoring
- Activity logs and admin actions

Detailed documentation: [Frontend README](frontend/README.md)

## Technology Stack

| Component | Technologies |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy |
| AI Integration | OpenRouter |
| Messaging | WhatsApp Business Cloud API |
| Database | SQLite |
| Frontend | React, TypeScript |
| Frontend Tooling | Vite, Tailwind CSS |
| API Communication | REST API |

## Project Structure

```text
Personal-WhatsApp-AI-Assistant/
│
├── backend/
│   ├── app.py
│   ├── admin_api.py
│   ├── ai.py
│   ├── smart_memory.py
│   ├── whatsapp.py
│   ├── requirements.txt
│   └── README.md
│
├── frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── README.md
│
├── .gitignore
└── README.md
```

## How It Works

1. A user sends a message to the WhatsApp Business number.
2. The backend receives the message through the WhatsApp webhook.
3. The assistant processes the message and relevant conversation context.
4. OpenRouter generates an AI response when appropriate.
5. The backend sends the response through the WhatsApp Cloud API.
6. The Admin Dashboard communicates with the backend to manage and monitor the assistant.

## Getting Started

### Prerequisites

- Python 3.14
- Node.js and npm
- Meta Developer account with WhatsApp Business Cloud API
- OpenRouter account and API key

### 1. Clone the Repository

```bash
git clone https://github.com/techsubhan/Personal-WhatsApp-AI-Assistant.git
cd Personal-WhatsApp-AI-Assistant
```

### 2. Configure and Run Backend

```bash
cd backend
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure the required environment variables in `.env`, then run:

```bash
python app.py
```

For complete backend configuration, read [backend/README.md](backend/README.md).

### 3. Configure and Run Frontend

Open another terminal:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open the local development URL shown by Vite, normally:

`http://localhost:5173`

For complete dashboard configuration, read [frontend/README.md](frontend/README.md).

## Security

- Never commit real `.env` files.
- Never expose WhatsApp access tokens or OpenRouter API keys.
- Keep `INTERNAL_API_KEY` private.
- Use the provided `.env.example` files as configuration templates.
- Keep private databases, logs, and personal data out of version control.

## Documentation

| Documentation | Description |
|---|---|
| [Backend README](backend/README.md) | Backend installation, configuration, APIs, Smart Memory, commands, and deployment |
| [Frontend README](frontend/README.md) | Dashboard setup, pages, API integration, and production build |

## Author

**Muhammad Subhan**

GitHub: https://github.com/techsubhan

---

**Personal WhatsApp AI Assistant** — Intelligent WhatsApp automation with a dedicated Admin Dashboard.
