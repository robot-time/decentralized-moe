//go:build darwin

package main

import "os/exec"

func browserCmd(url string) *exec.Cmd {
	return exec.Command("open", url)
}
