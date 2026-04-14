# Maker8 Scene Cut Review And FFmpeg-Only Solution (2026-04-14)

## 1. Context

Muc tieu: he thong can detect va cat video theo scene "vua du", uu tien tinh don gian va tinh on dinh van hanh. Theo yeu cau moi, giai phap khong dung `pyscenedetect`, chi dung truc tiep `ffmpeg`.

Log runtime cho thay `NORMALIZE` timeout 600 giay o video YouTube (dac biet nguon codec nang) la van de thuc te can xu ly.

## 2. Findings (Review)

### F1 - HIGH: Normalize dang re-encode toan bo file truoc khi co segmentation

- `NORMALIZE` re-encode full asset video.
- Hard timeout: 600s/asset.
- Video dai/codec nang de vuot nguong timeout.

Code refs:
- `src/maker8/pipeline/normalize.py` (`_VIDEO_TIMEOUT`, `_normalize_video`)

### F2 - HIGH: Chua co stage detect scene trong pipeline

- Flow hien tai chua co `SCENE_DETECT`.
- Viec cat hien tai phu thuoc `layer.trim` neu upstream da co san.

Code refs:
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/rendering/layers.py` (`layer.trim`)

### F3 - MEDIUM: Neu bo sung stage moi ma khong cap nhat dong bo se bi drift

Can cap nhat dong bo:
- `RenderStage` enum
- retryable stage set
- dry-run skip map
- metrics stage ordinal

Code refs:
- `src/maker8/models/common.py`
- `src/maker8/retry.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/observability/metrics.py`

## 3. Target State

1. Detect scene va cat segment bang `ffmpeg` (khong them dependency lon).
2. Normalize theo segment thay vi full video de giam timeout risk.
3. Editor8 co the hien thi va chinh tay cut points.
4. Neu detect/cat loi, job degrade co kiem soat (warning ro rang), khong fail cung mac dinh.

## 4. Proposed Architecture (FFmpeg Only)

### 4.1 New stage: `SCENE_DETECT`

Them stage moi sau `DOWNLOAD`, truoc `NORMALIZE`:

`VALIDATE -> RESOLVE_ASSETS -> DOWNLOAD -> SCENE_DETECT -> NORMALIZE -> TTS -> RENDER -> ...`

Nhiem vu:
- Chi xu ly asset `type=video`.
- Dung `ffmpeg` scene score (`select=gt(scene,threshold)`) de lay moc candidate.
- Parse `pts_time` tu output `showinfo`.
- Bo sung boundary dau/cuoi clip (`0` va `duration`).

Vi du detect:

```bash
ffmpeg -hide_banner -i input.mp4 \
  -vf "fps=3,scale=640:-2,select='gt(scene,0.35)',showinfo" \
  -an -f null -
```

Ghi chu:
- `fps=3` + `scale=640:-2` de detect nhanh, du "vua du".
- `threshold` can tune theo datasource.

### 4.2 Candidate post-processing policy

Sau khi co danh sach moc:
- Sap xep, loai duplicate, clamp vao [0, duration].
- Merge scene qua ngan theo `min_scene_len_sec`.
- Gioi han theo `max_scenes`.
- Neu khong co moc hop le: fallback 1 segment full video + warning.

### 4.3 Segment extraction strategy

Cat theo cap `(start_sec, end_sec)`:

```bash
# Fast path (stream copy)
ffmpeg -y -ss START -to END -i input.mp4 -c copy seg_000.mp4
```

Neu segment loi (keyframe/container):

```bash
# Fallback re-encode segment
ffmpeg -y -ss START -to END -i input.mp4 \
  -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 192k seg_000.mp4
```

### 4.4 Normalize strategy update

- Neu asset da co segments: `NORMALIZE` xu ly tung segment.
- Neu detect/cat fail hoac disabled: fallback flow cu (normalize full file).

Tac dong mong doi:
- Moi subprocess ngan hon.
- Giam xac suat timeout 600s.
- De retry/degrade theo tung segment.

### 4.5 Render integration

Thu tu uu tien:
1. Manual trim/cut points tu editor8.
2. Auto-candidates tu `SCENE_DETECT`.
3. Legacy trim behavior.

Payload cu khong co fields moi van chay nhu hien tai.

## 5. Contract And Data Model Changes

### 5.1 Asset source options

Mo rong `AssetSourceOptions`:
- `scene_detect_enabled: bool = false`
- `scene_detect_backend: str = "ffmpeg"` (chi chap nhan `ffmpeg`)
- `scene_detect_threshold: float | None`
- `scene_detect_min_scene_len_sec: float | None`
- `scene_detect_max_scenes: int | None`
- `scene_detect_sample_fps: int | None`
- `scene_detect_scale_width: int | None`

### 5.2 Pipeline context additions

Them fields:
- `scene_candidates: dict[str, list[SceneBoundary]]`
- `segmented_assets: dict[str, list[Path]]`
- `scene_detect_reports: list[dict[str, Any]]`

`SceneBoundary`:
- `start_sec: float`
- `end_sec: float`
- `score: float | None`

### 5.3 Warning codes

- `SCENE_DETECT_FAILED`
- `SCENE_DETECT_EMPTY`
- `SEGMENT_EXTRACT_FAILED`

## 6. Editor8 Integration

### 6.1 UI behavior

- Hien timeline + cut points auto detect.
- Cho phep user keo/chinh boundary thu cong.
- Nut apply vao scene trims.

### 6.2 Source-of-truth priority

1. Manual trims trong editor8.
2. Auto scene boundaries.
3. Legacy fallback.

## 7. Horizontal To Vertical Crop (Face-Safe)

## 7.1 Van de

Khi crop video ngang sang dung 9:16, neu crop window co dinh o giua frame thi rat de cat mat 1/2 guong mat bien tap vien.

## 7.2 Giai phap de xuat (uu tien an toan)

Dung 2 che do crop:

1. `fit_blur_bg` (mac dinh an toan):
- Khong cat manh vao chu the.
- Video goc scale theo chieu rong/chieu cao phu hop, phan thieu dien bang background blur.
- Gan nhu loai bo nguy co cat mat.

2. `smart_crop_face` (khi can full-frame dọc):
- Detect face bbox theo chu ky (vi du moi 5-10 frame).
- Tao track tam khuon mat theo thoi gian.
- Dat crop window 9:16 quanh tam track.
- Them margin an toan quanh mat (headroom + side padding).
- Lam muot toa do crop bang EMA de tranh giat.
- Neu confidence thap/khong thay mat: fallback ngay ve `fit_blur_bg`.

## 7.3 Face-safe guardrails

- Khong cho crop window cham vao bbox khuon mat vuot nguong (vd > 10% bbox bi cat).
- Neu 2+ frame lien tiep vi pham, tu dong:
  - shift crop window de giu tron mat, hoac
  - zoom out, hoac
  - fallback `fit_blur_bg`.

## 7.4 Vi tri tich hop

- Editor8:
  - preview crop mode truoc khi render.
  - user chon `fit_blur_bg` hoac `smart_crop_face`.
- Maker8:
  - apply crop mode trong `NORMALIZE`/preprocess.
  - ghi warning khi fallback.

## 8. Reliability And Observability

Them logs:
- `scene_detect.start/success/failure`
- `scene_segment.start/success/failure`
- `crop_mode.selected`
- `crop_face_track.lost` (khi mat track va fallback)

Them metrics:
- `maker8_scene_detect_duration_seconds`
- `maker8_scene_detect_failures_total{error_code}`
- `maker8_scene_segments_total{status}`
- `maker8_crop_fallback_total{reason}`

Cap nhat stage ordinal map de co `SCENE_DETECT`.

## 9. Rollout Plan

### Phase 1 - FFmpeg detect only
- Add stage `SCENE_DETECT`.
- Detect + report candidate cuts, chua cat that.

### Phase 2 - FFmpeg cut + normalize-by-segment
- Bat segment extraction cho canary jobs.
- Theo doi timeout rate, failure rate, disk I/O.

### Phase 3 - Crop mode rollout
- Bat `fit_blur_bg` truoc (safe default).
- Bat `smart_crop_face` theo feature flag.

### Phase 4 - Editor8 round-trip
- Editor8 cho phep chinh cut points va crop mode.
- Persist manual override va uu tien khi render.

## 10. Test Strategy

### 10.1 Unit tests

- `SCENE_DETECT` parse duoc `pts_time` tu ffmpeg output.
- Candidate merge/split policy dung.
- Segment extraction fast path + fallback path.
- Crop mode selector:
  - `fit_blur_bg`
  - `smart_crop_face`
  - fallback khi mat face track.

### 10.2 Integration tests

- E2E: `DOWNLOAD -> SCENE_DETECT -> NORMALIZE -> RENDER`.
- Video dai truoc day hay timeout, sau doi sang segment hoa phai giam timeout.
- Crop ngang->doc:
  - case 1 mat
  - case nhieu mat
  - case mat ra khoi khung.

### 10.3 Regression tests

- Payload cu khong co scene detect fields van pass.
- Legacy trim behavior khong vo.
- Warning/degrade van emit dung contract.

## 11. Acceptance Criteria

1. He thong detect va cat scene bang ffmpeg, khong can `pyscenedetect`.
2. Ty le `FFMPEG_TIMEOUT` tai `NORMALIZE` giam ro voi tap video dai.
3. Scene detect/cut loi thi job khong fail cung mac dinh, warning day du.
4. Co crop mode an toan de khong cat mat 1/2 guong mat:
   - default `fit_blur_bg`
   - `smart_crop_face` co fallback.
5. Backward compatibility voi request/spec cu.

## 12. Key Risks And Mitigations

- Over-cut do threshold thap -> tune threshold + min scene len + max scenes.
- Segment copy loi do keyframe -> re-encode fallback.
- Tang I/O do nhieu segment -> cleanup chat + gioi han segment count.
- Face detector confidence thap -> fallback `fit_blur_bg`.
- Contract drift editor8/maker8 -> cap nhat `render_contracts` truoc va bo test fixtures chung.

## 13. Recommended Next Implementation Sequence

1. Add `SCENE_DETECT` enum/stage skeleton + metrics/logging.
2. Implement ffmpeg scene detect parser + candidate manifest.
3. Implement ffmpeg segment extraction + fallback re-encode.
4. Update normalize de xu ly segment list.
5. Add crop modes (`fit_blur_bg` first, `smart_crop_face` behind flag).
6. Add tests + canary rollout + tune threshold.

