package main

import (
	"image"
	"image/color"
	"image/draw"
	"math"

	"github.com/getlantern/systray"
)

var windowOpen = false

func runTray(projectDir string) {
	systray.Run(func() { onTrayReady(projectDir) }, onTrayExit)
}

func onTrayReady(projectDir string) {
	systray.SetIcon(makeTrayIcon(true))
	systray.SetTitle("MoE Network")
	systray.SetTooltip("MoE Network — decentralized AI experts")

	mChat    := systray.AddMenuItem("Open Chat", "Open the chat interface")
	systray.AddSeparator()
	mStart   := systray.AddMenuItem("Start Network", "Start expert nodes")
	mStop    := systray.AddMenuItem("Stop Network", "Stop expert nodes")
	mStop.Disable()
	systray.AddSeparator()
	mQuit    := systray.AddMenuItem("Quit", "Quit MoE Network")

	go func() {
		for {
			select {
			case <-mChat.ClickedCh:
				openWindow(apiURL)

			case <-mStart.ClickedCh:
				go func() {
					postAPI(apiURL + "/api/nodes/start")
					systray.SetIcon(makeTrayIcon(true))
					mStart.Disable()
					mStop.Enable()
				}()

			case <-mStop.ClickedCh:
				go func() {
					postAPI(apiURL + "/api/nodes/stop")
					systray.SetIcon(makeTrayIcon(false))
					mStop.Disable()
					mStart.Enable()
				}()

			case <-mQuit.ClickedCh:
				systray.Quit()
			}
		}
	}()

	// Open chat window on first launch
	openWindow(apiURL)
}

func onTrayExit() {
	stopBackend()
}

// openWindow opens (or focuses) the WebView2 chat window.
func openWindow(url string) {
	if windowOpen {
		return
	}
	windowOpen = true
	go func() {
		defer func() { windowOpen = false }()
		showWebView(url)
	}()
}

// makeTrayIcon draws a 32×32 RGBA tray icon in the app's style.
func makeTrayIcon(running bool) []byte {
	const size = 32
	img := image.NewRGBA(image.Rect(0, 0, size, size))

	// Background circle
	bgAlpha := uint8(220)
	if !running {
		bgAlpha = 180
	}
	var bg color.RGBA
	if running {
		bg = color.RGBA{30, 41, 59, bgAlpha}
	} else {
		bg = color.RGBA{71, 85, 105, bgAlpha}
	}
	fillCircle(img, size/2, size/2, size/2-1, bg)

	// Expert dots (top, right, bottom, left)
	dotColors := []color.RGBA{
		{59, 130, 246, 255},  // blue
		{34, 197, 94, 255},   // green
		{249, 115, 22, 255},  // orange
		{168, 85, 247, 255},  // purple
	}
	r := 9.0
	cx, cy := float64(size/2), float64(size/2)
	angles := []float64{-math.Pi / 2, 0, math.Pi / 2, math.Pi}
	dotAlpha := uint8(255)
	if !running {
		dotAlpha = 120
	}

	for i, angle := range angles {
		dx := cx + r*math.Cos(angle)
		dy := cy + r*math.Sin(angle)
		c := dotColors[i]
		c.A = dotAlpha
		fillCircle(img, int(dx), int(dy), 3, c)
	}

	// Center hub
	hubAlpha := uint8(230)
	if !running {
		hubAlpha = 100
	}
	fillCircle(img, size/2, size/2, 2, color.RGBA{255, 255, 255, hubAlpha})

	// Convert to raw RGBA bytes (systray expects this on Windows)
	return rgbaToBytes(img)
}

func fillCircle(img *image.RGBA, cx, cy, r int, c color.Color) {
	for y := cy - r; y <= cy+r; y++ {
		for x := cx - r; x <= cx+r; x++ {
			dx, dy := x-cx, y-cy
			if dx*dx+dy*dy <= r*r {
				img.Set(x, y, c)
			}
		}
	}
}

func rgbaToBytes(img *image.RGBA) []byte {
	bounds := img.Bounds()
	w, h := bounds.Max.X, bounds.Max.Y
	out := make([]byte, w*h*4)
	tmp := image.NewRGBA(bounds)
	draw.Draw(tmp, bounds, img, bounds.Min, draw.Src)
	copy(out, tmp.Pix)
	return out
}

func postAPI(url string) {
	doPost(url)
}
