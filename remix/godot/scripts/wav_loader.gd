# wav_loader.gd: loads a PCM WAV file from disk at runtime (what Piper and most
# TTS engines write). 8/16-bit, mono/stereo. Returns null on anything else.
class_name WavLoader


static func load_file(path: String) -> AudioStreamWAV:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return null
	var bytes := f.get_buffer(f.get_length())
	f.close()
	return from_bytes(bytes)


static func from_bytes(b: PackedByteArray) -> AudioStreamWAV:
	if b.size() < 44 or b.slice(0, 4).get_string_from_ascii() != "RIFF" \
			or b.slice(8, 12).get_string_from_ascii() != "WAVE":
		return null
	var pos := 12
	var channels := 0
	var rate := 0
	var bits := 0
	var fmt_tag := 0
	var data := PackedByteArray()
	while pos + 8 <= b.size():
		var id := b.slice(pos, pos + 4).get_string_from_ascii()
		var size := b.decode_u32(pos + 4)
		var body := pos + 8
		if id == "fmt ":
			fmt_tag = b.decode_u16(body)
			channels = b.decode_u16(body + 2)
			rate = b.decode_u32(body + 4)
			bits = b.decode_u16(body + 14)
		elif id == "data":
			data = b.slice(body, mini(body + size, b.size()))
			break
		pos = body + size + (size & 1)
	if fmt_tag != 1 or data.is_empty() or channels < 1 or channels > 2 or rate <= 0:
		return null
	var s := AudioStreamWAV.new()
	s.mix_rate = rate
	s.stereo = channels == 2
	match bits:
		16:
			s.format = AudioStreamWAV.FORMAT_16_BITS
		8:
			# WAV 8-bit is unsigned; Godot wants signed.
			var signed := PackedByteArray()
			signed.resize(data.size())
			for i in data.size():
				signed[i] = (data[i] - 128) & 0xFF
			data = signed
			s.format = AudioStreamWAV.FORMAT_8_BITS
		_:
			return null
	s.data = data
	return s
