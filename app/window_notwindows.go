//go:build !windows

package main

// showWebView opens the chat UI in the system browser on macOS/Linux.
// No native WebView2 on these platforms.
func showWebView(url string) {
	openBrowser(url)
}
