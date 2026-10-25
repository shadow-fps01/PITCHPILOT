/**
 * PitchPilot PCM recorder — AudioWorkletProcessor.
 *
 * Captures microphone input, linearly downsamples it to 16 kHz mono,
 * converts Float32 samples to 16-bit PCM, and posts fixed-size frames
 * (2560 samples ≈ 160 ms) to the main thread as transferable buffers.
 * Also reports a throttled RMS mic level for the live overlay meter.
 *
 * Message shapes posted to the main thread:
 *   { type: "audio", samples: Int16Array }   — one 160 ms PCM frame
 *   { type: "level", rms: number }           — mic loudness, ~every 100 ms
 */

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 2560; // 160 ms at 16 kHz
const LEVEL_EVERY_FRAMES = 8; // ~100 ms at a 128-frame quantum... see process()

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Int16Array(FRAME_SAMPLES);
    this._bufferFill = 0;
    this._phase = 0; // fractional resampler position carried between calls
    this._processedFrames = 0;
  }

  process(inputs) {
    const input = inputs[0];
    const channel = input && input.length > 0 ? input[0] : null;
    if (!channel || channel.length === 0) {
      return true; // keep the processor alive during silences
    }

    const ratio = sampleRate / TARGET_SAMPLE_RATE; // e.g. 48000/16000 = 3

    // ── Linear resampling + Int16 conversion ──
    let pos = this._phase;
    while (pos < channel.length - 1) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const sample = channel[idx] * (1 - frac) + channel[idx + 1] * frac;
      const clamped = sample < -1 ? -1 : sample > 1 ? 1 : sample;
      this._buffer[this._bufferFill] = Math.round(clamped * 32767);
      this._bufferFill++;
      if (this._bufferFill === FRAME_SAMPLES) {
        const frame = this._buffer.slice();
        this._bufferFill = 0;
        this.port.postMessage({ type: "audio", samples: frame }, [frame.buffer]);
      }
      pos += ratio;
    }
    this._phase = pos - channel.length;

    // ── RMS mic level (throttled) ──
    this._processedFrames++;
    if (this._processedFrames % LEVEL_EVERY_FRAMES === 0) {
      let sum = 0;
      for (let i = 0; i < channel.length; i++) {
        sum += channel[i] * channel[i];
      }
      const rms = Math.sqrt(sum / channel.length);
      this.port.postMessage({ type: "level", rms: rms });
    }
    return true;
  }
}

registerProcessor("pcm-recorder", PcmRecorderProcessor);
