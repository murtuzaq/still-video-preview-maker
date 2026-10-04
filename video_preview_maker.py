"""Video Preview Maker

A Tkinter GUI tool that samples still frames from a video at evenly spaced
points along the timeline and composes them into a single grid "contact
sheet" image (e.g. a 5x5 grid of thumbnails in chronological order).
"""

import os
import threading
import tkinter as tk
from dataclasses import dataclass
from io import BytesIO
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

import cv2
from PIL import Image, ImageTk

VIDEO_FILETYPES = [
    ("Video files", "*.mp4 *.mkv *.mov *.avi *.wmv *.flv *.webm *.m4v"),
    ("All files", "*.*"),
]

JPEG_QUALITY = 90


@dataclass
class VideoInfo:
    path: str
    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration_sec(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.frame_count / self.fps


class VideoPreviewMaker(tk.Tk):
    MIN_ZOOM = 0.05
    MAX_ZOOM = 8.0
    ZOOM_STEP = 1.25

    def __init__(self):
        super().__init__()
        self.title("Video Preview Maker")
        self.geometry("1100x700")
        self.minsize(900, 560)

        self.video_info: Optional[VideoInfo] = None
        self.grid_image: Optional[Image.Image] = None  # full-resolution composed grid
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.zoom_level: float = 1.0

        self._build_layout()

    # ------------------------------------------------------------------ UI

    def _build_layout(self):
        container = ttk.Frame(self, padding=10)
        container.pack(fill="both", expand=True)

        left = ttk.Frame(container, width=340)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        right = ttk.Frame(container)
        right.pack(side="left", fill="both", expand=True)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_left_panel(self, parent):
        # --- Video selection ---
        sel_frame = ttk.LabelFrame(parent, text="Video", padding=10)
        sel_frame.pack(fill="x", pady=(0, 10))

        ttk.Button(sel_frame, text="Select Video...", command=self._select_video).pack(fill="x")
        self.video_label_var = tk.StringVar(value="No video selected")
        ttk.Label(sel_frame, textvariable=self.video_label_var, wraplength=300, foreground="#555").pack(
            fill="x", pady=(6, 0)
        )
        self.video_meta_var = tk.StringVar(value="")
        ttk.Label(sel_frame, textvariable=self.video_meta_var, wraplength=300, foreground="#555").pack(
            fill="x", pady=(2, 0)
        )

        # --- Sampling mode ---
        mode_frame = ttk.LabelFrame(parent, text="Sampling", padding=10)
        mode_frame.pack(fill="x", pady=(0, 10))

        self.mode_var = tk.StringVar(value="count")

        count_row = ttk.Frame(mode_frame)
        count_row.pack(fill="x", pady=2)
        ttk.Radiobutton(count_row, text="Total images:", variable=self.mode_var, value="count").pack(side="left")
        self.count_var = tk.StringVar(value="25")
        ttk.Entry(count_row, textvariable=self.count_var, width=8).pack(side="right")

        interval_row = ttk.Frame(mode_frame)
        interval_row.pack(fill="x", pady=2)
        ttk.Radiobutton(interval_row, text="Every N seconds:", variable=self.mode_var, value="interval").pack(
            side="left"
        )
        self.interval_var = tk.StringVar(value="30")
        ttk.Entry(interval_row, textvariable=self.interval_var, width=8).pack(side="right")

        size_row = ttk.Frame(mode_frame)
        size_row.pack(fill="x", pady=2)
        ttk.Radiobutton(size_row, text="Max output size (KB):", variable=self.mode_var, value="size").pack(
            side="left"
        )
        self.max_size_var = tk.StringVar(value="2000")
        ttk.Entry(size_row, textvariable=self.max_size_var, width=8).pack(side="right")

        ttk.Label(
            mode_frame,
            text="Max size mode auto-picks image count & interval\nto fit the target, using the scale/columns below.",
            foreground="#777",
            justify="left",
        ).pack(fill="x", pady=(6, 0))

        # --- Thumbnail / grid options ---
        grid_frame = ttk.LabelFrame(parent, text="Thumbnail & Grid", padding=10)
        grid_frame.pack(fill="x", pady=(0, 10))

        scale_row = ttk.Frame(grid_frame)
        scale_row.pack(fill="x", pady=2)
        ttk.Label(scale_row, text="Thumbnail scale (%):").pack(side="left")
        self.scale_var = tk.StringVar(value="100")
        ttk.Entry(scale_row, textvariable=self.scale_var, width=8).pack(side="right")

        cols_row = ttk.Frame(grid_frame)
        cols_row.pack(fill="x", pady=2)
        ttk.Label(cols_row, text="Columns:").pack(side="left")
        self.columns_var = tk.StringVar(value="5")
        ttk.Entry(cols_row, textvariable=self.columns_var, width=8).pack(side="right")

        spacing_row = ttk.Frame(grid_frame)
        spacing_row.pack(fill="x", pady=2)
        ttk.Label(spacing_row, text="Spacing (px):").pack(side="left")
        self.spacing_var = tk.StringVar(value="4")
        ttk.Entry(spacing_row, textvariable=self.spacing_var, width=8).pack(side="right")

        # --- Actions ---
        action_frame = ttk.Frame(parent)
        action_frame.pack(fill="x", pady=(0, 10))

        self.generate_btn = ttk.Button(action_frame, text="Generate Preview", command=self._on_generate)
        self.generate_btn.pack(fill="x")

        self.save_btn = ttk.Button(action_frame, text="Save Image...", command=self._on_save, state="disabled")
        self.save_btn.pack(fill="x", pady=(6, 0))

        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 6))

        self.status_var = tk.StringVar(value="Select a video to begin.")
        ttk.Label(parent, textvariable=self.status_var, wraplength=320, foreground="#333").pack(fill="x")

    def _build_right_panel(self, parent):
        preview_frame = ttk.LabelFrame(parent, text="Preview", padding=10)
        preview_frame.pack(fill="both", expand=True)

        toolbar = ttk.Frame(preview_frame)
        toolbar.pack(fill="x", pady=(0, 6))

        ttk.Button(toolbar, text="-", width=3, command=self._zoom_out).pack(side="left")
        ttk.Button(toolbar, text="+", width=3, command=self._zoom_in).pack(side="left", padx=(4, 0))
        self.zoom_label_var = tk.StringVar(value="100%")
        ttk.Label(toolbar, textvariable=self.zoom_label_var, width=6, anchor="center").pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(toolbar, text="Fit", command=self._zoom_fit).pack(side="left")
        ttk.Button(toolbar, text="100%", command=self._zoom_actual).pack(side="left", padx=(4, 0))
        ttk.Label(toolbar, text="(scroll to zoom, drag to pan)", foreground="#777").pack(
            side="left", padx=(10, 0)
        )

        canvas_frame = ttk.Frame(preview_frame)
        canvas_frame.pack(fill="both", expand=True)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(canvas_frame, background="#222222", highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")

        vbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.preview_canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.preview_canvas.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        self.preview_canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        self.preview_canvas.bind("<MouseWheel>", self._on_mousewheel)  # Windows / macOS
        self.preview_canvas.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.preview_canvas.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down
        self.preview_canvas.bind("<ButtonPress-1>", lambda e: self.preview_canvas.scan_mark(e.x, e.y))
        self.preview_canvas.bind(
            "<B1-Motion>", lambda e: self.preview_canvas.scan_dragto(e.x, e.y, gain=1)
        )

        self.preview_info_var = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self.preview_info_var, foreground="#555").pack(
            fill="x", pady=(6, 0)
        )

    # --------------------------------------------------------------- Logic

    def _select_video(self):
        path = filedialog.askopenfilename(title="Select a video file", filetypes=VIDEO_FILETYPES)
        if not path:
            return

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Could not open video:\n{path}")
            cap.release()
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()

        if frame_count <= 0 or fps <= 0:
            messagebox.showerror("Error", "Could not read video metadata (fps/frame count).")
            return

        self.video_info = VideoInfo(path=path, fps=fps, frame_count=frame_count, width=width, height=height)

        self.video_label_var.set(os.path.basename(path))
        duration = self.video_info.duration_sec
        mins, secs = divmod(int(duration), 60)
        self.video_meta_var.set(
            f"{width}x{height}  |  {fps:.2f} fps  |  {mins}m {secs}s  |  {frame_count} frames"
        )
        self.status_var.set("Video loaded. Adjust settings and click Generate Preview.")
        self.save_btn.config(state="disabled")
        self.grid_image = None
        self.preview_canvas.delete("all")
        self.preview_info_var.set("")

    def _read_settings(self):
        """Validate and return the current control values, or raise ValueError."""
        mode = self.mode_var.get()

        scale_pct = float(self.scale_var.get())
        if not (1 <= scale_pct <= 100):
            raise ValueError("Thumbnail scale must be between 1 and 100.")

        columns = int(self.columns_var.get())
        if columns < 1:
            raise ValueError("Columns must be at least 1.")

        spacing = int(self.spacing_var.get())
        if spacing < 0:
            raise ValueError("Spacing cannot be negative.")

        settings = {"mode": mode, "scale_pct": scale_pct, "columns": columns, "spacing": spacing}

        if mode == "count":
            count = int(self.count_var.get())
            if count < 1:
                raise ValueError("Total images must be at least 1.")
            settings["count"] = count
        elif mode == "interval":
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError("Interval must be greater than 0 seconds.")
            settings["interval"] = interval
        elif mode == "size":
            max_kb = float(self.max_size_var.get())
            if max_kb <= 0:
                raise ValueError("Max output size must be greater than 0 KB.")
            settings["max_kb"] = max_kb
        else:
            raise ValueError("Unknown sampling mode.")

        return settings

    def _on_generate(self):
        if self.video_info is None:
            messagebox.showwarning("No video", "Please select a video first.")
            return
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return

        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.generate_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self.progress.config(value=0, maximum=100)
        self.status_var.set("Generating preview...")

        self.worker_thread = threading.Thread(target=self._generate_worker, args=(settings,), daemon=True)
        self.worker_thread.start()

    def _generate_worker(self, settings):
        try:
            if settings["mode"] == "size":
                grid_img, used_count = self._build_grid_for_target_size(settings)
            else:
                timestamps = self._compute_timestamps(settings)
                frames = self._extract_frames(timestamps)
                grid_img = self._compose_grid(frames, settings["columns"], settings["scale_pct"], settings["spacing"])
                used_count = len(frames)

            self.after(0, lambda: self._on_generate_done(grid_img, used_count, settings))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.after(0, lambda: self._on_generate_error(str(exc)))

    def _on_generate_done(self, grid_img, used_count, settings):
        self.grid_image = grid_img
        self._zoom_fit()

        size_kb = self._estimate_jpeg_size_kb(grid_img)
        rows = -(-used_count // settings["columns"])  # ceil div
        self.preview_info_var.set(
            f"{used_count} images  |  {settings['columns']} cols x {rows} rows  |  "
            f"{grid_img.width}x{grid_img.height}px  |  ~{size_kb:.0f} KB (JPEG)"
        )
        self.status_var.set("Preview ready.")
        self.progress.config(value=100)
        self.generate_btn.config(state="normal")
        self.save_btn.config(state="normal")

    def _on_generate_error(self, message):
        self.status_var.set("Failed to generate preview.")
        self.progress.config(value=0)
        self.generate_btn.config(state="normal")
        messagebox.showerror("Error", message)

    # ---------------------------------------------------------- Extraction

    def _compute_timestamps(self, settings) -> List[float]:
        duration = self.video_info.duration_sec
        mode = settings["mode"]

        if mode == "count":
            count = settings["count"]
            if count == 1:
                return [duration / 2]
            # Evenly spaced, avoiding the very first/last frame edge artifacts.
            step = duration / count
            return [step * (i + 0.5) for i in range(count)]

        if mode == "interval":
            interval = settings["interval"]
            timestamps = []
            t = interval / 2
            while t < duration:
                timestamps.append(t)
                t += interval
            if not timestamps:
                timestamps = [duration / 2]
            return timestamps

        raise ValueError(f"Unsupported mode for timestamp computation: {mode}")

    def _extract_frames(self, timestamps: List[float]) -> List[Image.Image]:
        cap = cv2.VideoCapture(self.video_info.path)
        if not cap.isOpened():
            raise RuntimeError("Could not reopen video for frame extraction.")

        frames = []
        total = len(timestamps)
        try:
            for i, t in enumerate(timestamps):
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame_bgr = cap.read()
                if not ok or frame_bgr is None:
                    continue
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(frame_rgb))

                progress_pct = (i + 1) / total * 90  # reserve last 10% for composition
                self.after(0, lambda p=progress_pct: self.progress.config(value=p))
        finally:
            cap.release()

        if not frames:
            raise RuntimeError("No frames could be extracted from this video.")
        return frames

    # ---------------------------------------------------------- Composition

    def _compose_grid(self, frames: List[Image.Image], columns: int, scale_pct: float, spacing: int) -> Image.Image:
        columns = max(1, min(columns, len(frames)))
        scale = scale_pct / 100.0

        thumbs = []
        for frame in frames:
            w, h = frame.size
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            thumbs.append(frame.resize(new_size, Image.LANCZOS))

        thumb_w, thumb_h = thumbs[0].size
        rows = -(-len(thumbs) // columns)  # ceil div

        grid_w = columns * thumb_w + (columns + 1) * spacing
        grid_h = rows * thumb_h + (rows + 1) * spacing

        grid_img = Image.new("RGB", (grid_w, grid_h), color=(20, 20, 20))
        for idx, thumb in enumerate(thumbs):
            row, col = divmod(idx, columns)
            x = spacing + col * (thumb_w + spacing)
            y = spacing + row * (thumb_h + spacing)
            grid_img.paste(thumb, (x, y))

        return grid_img

    def _estimate_jpeg_size_kb(self, img: Image.Image) -> float:
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return len(buf.getvalue()) / 1024.0

    def _build_grid_for_target_size(self, settings):
        """Search for the largest image count that keeps the composed grid
        under the target JPEG size, using the fixed scale/columns/spacing.

        Uses exponential growth to bracket the target, then binary search
        within the bracket to converge on the maximum count that still fits,
        so the output uses as much of the size budget as possible instead of
        stopping at a coarse, arbitrarily capped guess.
        """
        target_kb = settings["max_kb"]
        columns = settings["columns"]
        scale_pct = settings["scale_pct"]
        spacing = settings["spacing"]
        duration = self.video_info.duration_sec

        # Don't bother sampling more distinct images than the video has frames,
        # and keep a sane ceiling so the search can't run away on long videos.
        hard_cap = max(1, min(500, int(duration * self.video_info.fps)))

        evaluated = 0
        max_evaluations = 24  # generous bound for exponential + binary search phases

        def size_for_count(count):
            nonlocal evaluated
            evaluated += 1
            timestamps = self._compute_timestamps({"mode": "count", "count": count})
            frames = self._extract_frames(timestamps)
            grid_img = self._compose_grid(frames, columns, scale_pct, spacing)
            size_kb = self._estimate_jpeg_size_kb(grid_img)
            progress_pct = min(90, evaluated / max_evaluations * 90)
            self.after(0, lambda p=progress_pct: self.progress.config(value=p))
            return grid_img, size_kb, len(frames)

        best_img, best_size, best_count = size_for_count(1)
        if best_size > target_kb:
            # Even a single image exceeds the target; this is the best we can do.
            return best_img, best_count

        # Phase 1: exponentially grow the count until the grid exceeds the
        # target (or we hit the hard cap), tracking the last count that fit.
        lo_count = 1
        hi_count = None
        count = 1
        while True:
            count = min(count * 2, hard_cap)
            img, size_kb, actual_count = size_for_count(count)
            if size_kb <= target_kb:
                best_img, best_size, best_count = img, size_kb, actual_count
                lo_count = count
                if count >= hard_cap:
                    return best_img, best_count
            else:
                hi_count = count
                break

        # Phase 2: binary search between lo_count (fits) and hi_count (too big)
        # to find the precise maximum count that still fits the target.
        while hi_count - lo_count > 1:
            mid = (lo_count + hi_count) // 2
            img, size_kb, actual_count = size_for_count(mid)
            if size_kb <= target_kb:
                lo_count = mid
                best_img, best_size, best_count = img, size_kb, actual_count
            else:
                hi_count = mid

        return best_img, best_count

    # -------------------------------------------------------------- Preview

    def _render_at_zoom(self):
        """Redraw the canvas with self.grid_image scaled to self.zoom_level."""
        img = self.grid_image
        img_w, img_h = img.size
        scaled_w = max(1, round(img_w * self.zoom_level))
        scaled_h = max(1, round(img_h * self.zoom_level))

        resample = Image.LANCZOS if self.zoom_level <= 1 else Image.NEAREST
        scaled = img.resize((scaled_w, scaled_h), resample)
        self.preview_photo = ImageTk.PhotoImage(scaled)

        self.preview_canvas.delete("all")
        canvas_w = max(self.preview_canvas.winfo_width(), scaled_w)
        canvas_h = max(self.preview_canvas.winfo_height(), scaled_h)
        self.preview_canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))
        self.preview_canvas.create_image(0, 0, image=self.preview_photo, anchor="nw")

        self.zoom_label_var.set(f"{round(self.zoom_level * 100)}%")

    def _zoom_at(self, new_zoom: float, canvas_x: int, canvas_y: int):
        if self.grid_image is None:
            return
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if abs(new_zoom - self.zoom_level) < 1e-6:
            return

        # Image-space point currently under (canvas_x, canvas_y), so we can
        # keep it under the cursor/center after the zoom level changes.
        img_x = self.preview_canvas.canvasx(canvas_x) / self.zoom_level
        img_y = self.preview_canvas.canvasy(canvas_y) / self.zoom_level

        self.zoom_level = new_zoom
        self._render_at_zoom()

        img_w, img_h = self.grid_image.size
        scaled_w = img_w * self.zoom_level
        scaled_h = img_h * self.zoom_level
        if scaled_w > 0:
            frac_x = max(0.0, (img_x * self.zoom_level - canvas_x)) / scaled_w
            self.preview_canvas.xview_moveto(frac_x)
        if scaled_h > 0:
            frac_y = max(0.0, (img_y * self.zoom_level - canvas_y)) / scaled_h
            self.preview_canvas.yview_moveto(frac_y)

    def _canvas_center(self):
        return self.preview_canvas.winfo_width() // 2, self.preview_canvas.winfo_height() // 2

    def _zoom_in(self):
        cx, cy = self._canvas_center()
        self._zoom_at(self.zoom_level * self.ZOOM_STEP, cx, cy)

    def _zoom_out(self):
        cx, cy = self._canvas_center()
        self._zoom_at(self.zoom_level / self.ZOOM_STEP, cx, cy)

    def _zoom_actual(self):
        cx, cy = self._canvas_center()
        self._zoom_at(1.0, cx, cy)

    def _zoom_fit(self):
        if self.grid_image is None:
            return
        self.preview_canvas.update_idletasks()
        canvas_w = self.preview_canvas.winfo_width() or 1
        canvas_h = self.preview_canvas.winfo_height() or 1
        img_w, img_h = self.grid_image.size
        fit_zoom = min(canvas_w / img_w, canvas_h / img_h)
        self.zoom_level = max(self.MIN_ZOOM, min(self.MAX_ZOOM, fit_zoom))
        self._render_at_zoom()
        self.preview_canvas.xview_moveto(0)
        self.preview_canvas.yview_moveto(0)

    def _on_mousewheel(self, event):
        if self.grid_image is None:
            return
        if getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            factor = 1 / self.ZOOM_STEP
        else:
            factor = self.ZOOM_STEP
        self._zoom_at(self.zoom_level * factor, event.x, event.y)

    def _on_save(self):
        if self.grid_image is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save preview image",
            defaultextension=".jpg",
            filetypes=[("JPEG image", "*.jpg"), ("PNG image", "*.png")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".png"):
                self.grid_image.save(path, format="PNG")
            else:
                self.grid_image.save(path, format="JPEG", quality=JPEG_QUALITY)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", f"Could not save image:\n{exc}")
            return
        self.status_var.set(f"Saved to {path}")


if __name__ == "__main__":
    app = VideoPreviewMaker()
    app.mainloop()
