//go:build windows

package main

import "os/exec"

func browserCmd(url string) *exec.Cmd {
	return exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
}
