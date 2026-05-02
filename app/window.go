package main

import (
	"bytes"
	"fmt"
	"net/http"
	"time"
)

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

// doPost fires a fire-and-forget POST with an empty JSON body.
func doPost(url string) {
	client := &http.Client{Timeout: 5 * time.Second}
	_, _ = client.Post(url, "application/json", bytes.NewReader([]byte("{}")))
}

// openBrowser opens url in the system default browser.
func openBrowser(url string) {
	cmd := browserCmd(url)
	if cmd != nil {
		_ = cmd.Start()
	}
}
