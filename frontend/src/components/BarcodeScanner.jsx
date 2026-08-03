import { useEffect, useRef, useState } from "react";
import { BrowserCodeReader, BrowserMultiFormatReader } from "@zxing/browser";

// Backlog B4.1 (author-requested 2026-08-01): a camera-based barcode
// scanner, using ZXing -- a pure-JS decoder that runs entirely in the
// browser, no server round trip needed to read the barcode itself
// (only the decoded number is sent to the backend, via GET /api/
// inventory/barcode-lookup).
//
// Deliberately the ONLY implementation path here, not "native
// BarcodeDetector Web API with a ZXing fallback": verified live (2026)
// that Safari, and every browser on iOS/iPadOS (WebKit is the only
// rendering engine Apple allows there, so this applies to "Chrome for
// iOS"/"Firefox for iOS" too, not just Safari itself), do not implement
// BarcodeDetector, with no Apple-announced timeline to add it. The
// author explicitly needs this to work on a phone and an iPad with no
// dedicated scanner peripheral -- branching on native-API availability
// would leave iOS users on a second, far-less-exercised code path for
// no real benefit, so this app always uses ZXing, on every platform.
//
// A desktop/laptop webcam can decode a standard UPC-A/EAN-13 grocery
// barcode too, just less reliably than a phone camera at close range
// (lower resolution, harder to get the barcode in focus at typical
// webcam distance) -- a real, physical-hardware limitation, not
// something this component can code around. The manual entry field
// below exists specifically for that case (and for a barcode that's too
// worn/damaged to scan at all).
export default function BarcodeScanner({ onDetected, onClose }) {
  const videoRef = useRef(null);
  const readerRef = useRef(null);
  const controlsRef = useRef(null);
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [error, setError] = useState(null);
  const [manualBarcode, setManualBarcode] = useState("");
  // True once camera permission has been granted and device enumeration
  // has been attempted -- see the two effects below for why decoding is
  // gated on this rather than on a device id.
  const [ready, setReady] = useState(false);

  // `onDetected` is held in a ref rather than depended on directly.
  //
  // The parent recreates that callback on every render, so listing it in
  // the decode effect's dependency array tore the camera down and
  // restarted it on every parent state change -- visible as a flickering
  // preview and, on slower devices, a scanner that never stayed up long
  // enough to decode anything. The ref keeps the effect stable while
  // still always calling the current callback.
  const onDetectedRef = useRef(onDetected);
  useEffect(() => {
    onDetectedRef.current = onDetected;
  }, [onDetected]);

  // Step 1: request camera permission, THEN enumerate devices.
  //
  // Order is the whole bug. WebKit deliberately withholds device identity
  // until camera access has been granted -- on iOS and iPadOS (where
  // every browser is WebKit, including "Chrome" and "Firefox"),
  // `enumerateDevices()` on an unpermissioned origin returns entries with
  // empty labels and empty/obfuscated `deviceId` values.
  //
  // This component used to call `listVideoInputDevices()` first and then
  // gate everything on the resulting `deviceId`. On iOS that id was `""`,
  // which is falsy, so the decode effect below returned immediately and
  // `getUserMedia` was never called at all. No permission prompt, no
  // video element, no error -- the camera simply never engaged, which is
  // exactly what was reported. It failed over HTTPS too, so it was never
  // the secure-context problem it was mistaken for.
  //
  // Requesting a stream first triggers the prompt and unlocks real device
  // labels and ids, so the camera picker below works on the second pass.
  useEffect(() => {
    // Checked first and unconditionally: WebKit keeps
    // `navigator.mediaDevices.getUserMedia` PRESENT on an insecure origin
    // but never settles calls made through it, so a feature-detection
    // check passes and then hangs forever with nothing shown.
    if (window.isSecureContext === false) {
      setError(
        "Camera access needs HTTPS (or localhost) -- this page was loaded over a plain, non-secure connection. Type the barcode number below instead."
      );
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser doesn't support camera access. Type the barcode number below instead.");
      return;
    }

    readerRef.current = new BrowserMultiFormatReader();
    let cancelled = false;

    async function requestPermissionThenEnumerate() {
      let stream;
      try {
        // `ideal`, not `exact`: a laptop with only a front-facing webcam
        // must still get a stream rather than an OverconstrainedError.
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
        });
      } catch (err) {
        if (!cancelled) setError(cameraErrorMessage(err));
        return;
      }
      // The probe stream's only job was to unlock permission. Release it
      // immediately -- ZXing opens its own, and leaving this one running
      // means two live camera tracks and a permission indicator that
      // never goes out.
      stream.getTracks().forEach((track) => track.stop());
      if (cancelled) return;

      let list = [];
      try {
        list = await BrowserCodeReader.listVideoInputDevices();
      } catch {
        // Enumeration is only needed for the camera PICKER. Failing it is
        // not fatal: falling through with an empty device id makes ZXing
        // use the browser's default camera, which is the right one on
        // most phones anyway.
        list = [];
      }
      if (cancelled) return;

      setDevices(list);
      // Most phones and tablets label the rear camera "back"/"rear"/
      // "environment". Prefer it -- it is the one pointed at the product
      // in your hand. The browser's own first entry is often the selfie
      // camera.
      const rear = list.find((d) => /back|rear|environment/i.test(d.label || ""));
      setDeviceId((rear || list[0])?.deviceId || "");
      setReady(true);
    }

    requestPermissionThenEnumerate();

    return () => {
      cancelled = true;
      controlsRef.current?.stop();
      controlsRef.current = null;
    };
  }, []);

  // Step 2: decode. Runs once permission has been granted, whether or not
  // enumeration produced a usable device id -- an empty id tells ZXing to
  // use the default camera, which is a working scanner rather than the
  // blank panel the old `if (!deviceId) return` produced.
  useEffect(() => {
    if (!ready || !videoRef.current || !readerRef.current) return;
    let stopped = false;
    setError(null);

    readerRef.current
      .decodeFromVideoDevice(deviceId || undefined, videoRef.current, (result, err, controls) => {
        // Captured on every callback so the cleanup below can stop the
        // stream even if the component unmounts before the first decode.
        // Previously `controlsRef` stayed null until this fired, so a
        // quick cancel leaked the MediaStream and left the camera light on.
        controlsRef.current = controls;
        if (stopped) {
          controls.stop();
          return;
        }
        if (result) {
          stopped = true;
          controls.stop();
          onDetectedRef.current(result.getText());
          return;
        }
        // ZXing invokes this on every video frame, decoded or not -- a
        // "NotFoundException" just means no barcode was readable in THAT
        // frame, which is the normal state while hunting for one. Only a
        // genuine stream/device failure is worth surfacing.
        if (err && err.name !== "NotFoundException") {
          setError(cameraErrorMessage(err));
        }
      })
      .catch((err) => setError(cameraErrorMessage(err)));

    return () => {
      stopped = true;
      controlsRef.current?.stop();
      controlsRef.current = null;
    };
  }, [deviceId, ready]);

  function handleManualSubmit(e) {
    e.preventDefault();
    if (!manualBarcode.trim()) return;
    controlsRef.current?.stop();
    onDetected(manualBarcode.trim());
  }

  function handleClose() {
    controlsRef.current?.stop();
    onClose();
  }

  return (
    <div className="barcode-scanner">
      {devices.length > 1 && (
        <label className="barcode-scanner-device">
          Camera
          <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
            {devices.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `Camera ${i + 1}`}
              </option>
            ))}
          </select>
        </label>
      )}
      {/* Rendered as soon as permission is granted, not once a device id
          exists. On iOS the id is legitimately empty and the browser picks
          the default camera -- gating the <video> on it left an empty
          panel with the camera running behind it. */}
      {ready && !error && (
        <div className="barcode-scanner-video-wrap">
          <video ref={videoRef} className="barcode-scanner-video" muted playsInline autoPlay />
        </div>
      )}
      {!ready && !error && <p className="hint">Waiting for camera permission...</p>}
      {error && <p className="error-text">{error}</p>}
      {ready && !error && (
        <p className="hint">Point the camera at a barcode -- it scans automatically, no button to press.</p>
      )}
      <form className="barcode-scanner-manual" onSubmit={handleManualSubmit}>
        <input
          placeholder="Or type the barcode number"
          aria-label="Barcode number"
          value={manualBarcode}
          onChange={(e) => setManualBarcode(e.target.value)}
          inputMode="numeric"
        />
        <button type="submit" className="btn btn-secondary btn-sm" disabled={!manualBarcode.trim()}>
          Use this number
        </button>
      </form>
      <div className="form-actions">
        <button type="button" className="btn btn-secondary" onClick={handleClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function cameraErrorMessage(err) {
  if (err?.name === "NotAllowedError") {
    return "Camera access was denied. Allow camera access in your browser's settings, or type the barcode number below instead.";
  }
  if (err?.name === "NotFoundError" || err?.name === "OverconstrainedError") {
    return "No camera found on this device. Type the barcode number below instead.";
  }
  if (err?.name === "NotReadableError") {
    // Another app (or another tab) already holds the camera. Worth its own
    // message: "could not start the camera" sends people to their
    // permission settings, which is not the problem.
    return "The camera is in use by another app or tab. Close it and try again, or type the barcode number below.";
  }
  return `Could not start the camera (${err?.message || err}). Type the barcode number below instead.`;
}
