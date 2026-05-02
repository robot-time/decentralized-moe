package main

import (
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"
)

const apiPort = "8080"
const apiURL  = "http://127.0.0.1:" + apiPort

var pythonProc *exec.Cmd

func main() {
	exePath, err := os.Executable()
	if err != nil {
		log.Fatal(err)
	}
	exeDir := filepath.Dir(exePath)

	if err := startBackend(exeDir); err != nil {
		log.Fatalf("Failed to start backend: %v", err)
	}
	defer stopBackend()

	// Wait up to 30 s for the API server to be ready
	if err := waitForAPI(apiURL+"/api/status", 30*time.Second); err != nil {
		log.Fatalf("Backend did not start in time: %v", err)
	}

	// Clean shutdown on Ctrl-C / SIGTERM
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sig
		stopBackend()
		os.Exit(0)
	}()

	// Tray + WebView2 window (blocks until Quit)
	runTray(exeDir)
}

// startBackend launches either the bundled backend.exe (distribution) or
// "python api_server.py" (development, when backend.exe is not present).
func startBackend(exeDir string) error {
	backendExe := backendBinaryName()
	bundled    := filepath.Join(exeDir, backendExe)

	var cmd *exec.Cmd
	if _, err := os.Stat(bundled); err == nil {
		// Distribution mode: run the bundled PyInstaller binary
		cmd = exec.Command(bundled)
		cmd.Dir = exeDir
	} else {
		// Development mode: run Python directly from the project root
		projectDir := filepath.Dir(exeDir) // app/ -> project root
		python     := pythonExe()
		script     := filepath.Join(projectDir, "api_server.py")
		cmd = exec.Command(python, script)
		cmd.Dir = projectDir
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
	}

	pythonProc = cmd
	return cmd.Start()
}

func stopBackend() {
	if pythonProc != nil && pythonProc.Process != nil {
		_ = pythonProc.Process.Kill()
	}
}

func backendBinaryName() string {
	if runtime.GOOS == "windows" {
		return "backend.exe"
	}
	return "backend"
}

func pythonExe() string {
	if runtime.GOOS == "windows" {
		if path, err := exec.LookPath("python"); err == nil {
			return path
		}
	}
	return "python3"
}
