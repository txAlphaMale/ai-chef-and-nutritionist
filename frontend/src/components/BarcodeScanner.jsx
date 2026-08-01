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

  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError(
        window.isSecureContext === false
          ? "Camera access needs HTTPS (or localhost) -- this page was loaded over a plain, non-secure connection. Type the barcode number below instead."
          : "This browser doesn't support camera access. Type the barcode number below instead."
      );
      return;
    }

    readerRef.current = new BrowserMultiFormatReader();
    let cancelled = false;

    BrowserCodeReader.listVideoInputDevices()
      .then((list) => {
        if (cancelled) return;
        if (list.length === 0) {
          setError("No camera found on this device. Type the barcode number below instead.");
          return;
        }
        setDevices(list);
        // Most phones/tablets label their rear camera "back"/"environment"
        // -- prefer it (it's the one actually useful for scanning a
        // product in your hand) over whichever device the browser lists
        // first, which on mobile is often the front-facing camera.
        const rear = list.find((d) => /back|rear|environment/i.test(d.label));
        setDeviceId((rear || list[0]).deviceId);
      })
      .catch((err) => {
        if (!cancelled) setError(cameraErrorMessage(err));
      });

    return () => {
      cancelled = true;
      controlsRef.current?.stop();
    };
  }, []);

  useEffect(() => {
    if (!deviceId || !videoRef.current || !readerRef.current) return;
    let stopped = false;
    setError(null);

    readerRef.current
      .decodeFromVideoDevice(deviceId, videoRef.current, (result, err, controls) => {
        controlsRef.current = controls;
        if (result && !stopped) {
          stopped = true;
          controls.stop();
          onDetected(result.getText());
          return;
        }
        // ZXing calls this callback on every video frame, succeeding or
        // not -- a "NotFoundException" just means no barcode was
        // decodable in THAT frame, which is the normal, constant state
        // while scanning, not an error. Only a genuine stream/device
        // failure is worth surfacing.
        if (err && err.name !== "NotFoundException") {
          setError(cameraErrorMessage(err));
        }
      })
      .catch((err) => setError(cameraErrorMessage(err)));

    return () => {
      stopped = true;
      controlsRef.current?.stop();
    };
  }, [deviceId, onDetected]);

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
      {deviceId && (
        <div className="barcode-scanner-video-wrap">
          <video ref={videoRef} className="barcode-scanner-video" muted playsInline />
        </div>
      )}
      {error && <p className="error-text">{error}</p>}
      {deviceId && !error && (
        <p className="hint">Point the camera at a barcode -- it scans automatically, no button to press.</p>
      )}
      <form className="barcode-scanner-manual" onSubmit={handleManualSubmit}>
        <input
          placeholder="Or type the barcode number"
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
  return `Could not start the camera (${err?.message || err}). Type the barcode number below instead.`;
}
