//go:build windows

package main

import webview "github.com/jchv/go-webview2"

// showWebView opens a native WebView2 window. Blocks until closed.
func showWebView(url string) {
	w := webview.New(false)
	if w == nil {
		openBrowser(url)
		return
	}
	defer w.Destroy()
	w.SetTitle("MoE Network")
	w.SetSize(1100, 740, webview.HintNone)
	w.Navigate(url)
	w.Run()
}
