//go:build linux

package main

import "os/exec"

func browserCmd(url string) *exec.Cmd {
	return exec.Command("xdg-open", url)
}
