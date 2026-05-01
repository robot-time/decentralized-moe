package main

import (
	"bytes"
	"fmt"
	"net/http"
	"time"

	webview "github.com/jchv/go-webview2"
)

// showWebView opens a native WebView2 window pointing at url.
// Blocks until the window is closed.
func showWebView(url string) {
	w := webview.NewWithOptions(webview.WebViewOptions{
		Debug:  false,
		Window: nil,
		AutoFocus: true,
	})
	if w == nil {
		// WebView2 runtime not installed — fall back to browser
		openBrowser(url)
		return
	}
	defer w.Destroy()

	w.SetTitle("MoE Network")
	w.SetSize(1100, 740, webview.HintNone)
	w.Navigate(url)
	w.Run()
}

// waitForAPI polls url until it returns 200 or timeout expires.
func waitForAPI(url string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 2 * time.Second}
	for time.Now().Before(deadline) {
		resp, err := client.Get(url)
		if err == nil && resp.StatusCode == 200 {
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for %s", url)
}

// doPost fires a POST with no body and ignores the response.
func doPost(url string) {
	client := &http.Client{Timeout: 5 * time.Second}
	_, _ = client.Post(url, "application/json", bytes.NewReader([]byte("{}")))
}

// openBrowser opens the default browser as a fallback when WebView2 is unavailable.
func openBrowser(url string) {
	// Use rundll32 on Windows to open the default browser
	_ = fmt.Sprintf("open %s", url)
	cmd := browserCmd(url)
	if cmd != nil {
		_ = cmd.Start()
	}
}
