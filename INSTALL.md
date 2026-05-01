# Install & Run

## Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/download) installed and running
- [Node.js](https://nodejs.org) 18+ (to build the UI)
- [Go](https://go.dev/dl/) 1.22+ (to build the shell)

## 1 — Python dependencies
```
pip install -r requirements.txt
```

## 2 — Build the React UI
```
cd ui
npm install
npm run build
cd ..
```

## 3 — Build the Go shell
```
cd app
go mod tidy
go build -o ../moe.exe -ldflags "-H windowsgui" .
cd ..
```

## 4 — Run
Double-click `moe.exe`, or:
```
./moe.exe
```

The Go shell will:
1. Start `api_server.py` in the background
2. Show a tray icon in the system notification area
3. Open the chat window automatically

## Development (hot-reload UI)
```
# Terminal 1 — Python API
python api_server.py

# Terminal 2 — React dev server (proxies /api to Python)
cd ui && npm run dev
```
Then open http://localhost:5173 in your browser.
