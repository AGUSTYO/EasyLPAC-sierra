target = "internal/atclient/atclient.go"

with open(target, "r", encoding="utf-8") as f:
    src = f.read()

old_method = """// CommandWithTimeout is Command with a per-call final-response timeout,
// used for data commands (APDU execution) that may legitimately take
// longer than control commands.
func (c *Client) CommandWithTimeout(cmd string, expectPrefix string, timeout time.Duration) (*Result, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.closed {
		return nil, errors.New("atclient: client closed")
	}
	if c.invalid != nil {
		return nil, fmt.Errorf("%w: %v", ErrDesynchronized, c.invalid)
	}

	if _, err := c.port.Write([]byte(cmd + "\\r")); err != nil {
		return c.fail("write: %v", err)
	}

	if timeout <= 0 {
		timeout = c.opts.Timeout
	}
	deadline := time.Now().Add(timeout)
	res := &Result{}
	buf := make([]byte, readChunk)

	for {
		if time.Now().After(deadline) {
			return c.fail("timeout waiting for final response to %q", cmd)
		}
		n, err := c.port.Read(buf)
		if err != nil {
			if isTimeout(err) {
				continue
			}
			return c.fail("read: %v", err)
		}

		lines := c.appendAndSplit(buf[:n])
		if c.invalid != nil {
			return nil, c.invalid
		}
		for _, line := range lines {
			switch {
			case line == "" || line == cmd: // empty line or echo
			case line == "OK":
				return res, nil
			case line == "ERROR":
				return nil, errors.New("atclient: modem returned ERROR")
			case strings.HasPrefix(line, "+CME ERROR:"), strings.HasPrefix(line, "+CMS ERROR:"):
				return nil, fmt.Errorf("atclient: modem returned %s", line)
			case expectPrefix != "" && strings.HasPrefix(line, expectPrefix):
				res.Value = unquote(strings.TrimSpace(strings.TrimPrefix(line, expectPrefix)))
			case strings.HasPrefix(line, "+"):
				res.URCs = append(res.URCs, line)
				if c.opts.URC != nil {
					c.opts.URC(line)
				}
			default:
				// Non-standard informational line; keep as diagnostic.
				res.URCs = append(res.URCs, line)
			}
		}
	}
}"""

new_method = """func (c *Client) execSingleCommand(cmd string, expectPrefix string, timeout time.Duration) (*Result, error) {
	if _, err := c.port.Write([]byte(cmd + "\\r")); err != nil {
		return c.fail("write: %v", err)
	}

	if timeout <= 0 {
		timeout = c.opts.Timeout
	}
	deadline := time.Now().Add(timeout)
	res := &Result{}
	buf := make([]byte, readChunk)

	for {
		if time.Now().After(deadline) {
			return c.fail("timeout waiting for final response to %q", cmd)
		}
		n, err := c.port.Read(buf)
		if err != nil {
			if isTimeout(err) {
				continue
			}
			return c.fail("read: %v", err)
		}

		lines := c.appendAndSplit(buf[:n])
		if c.invalid != nil {
			return nil, c.invalid
		}
		for _, line := range lines {
			switch {
			case line == "" || line == cmd:
			case line == "OK":
				return res, nil
			case line == "ERROR":
				return nil, errors.New("atclient: modem returned ERROR")
			case strings.HasPrefix(line, "+CME ERROR:"), strings.HasPrefix(line, "+CMS ERROR:"):
				return nil, fmt.Errorf("atclient: modem returned %s", line)
			case expectPrefix != "" && strings.HasPrefix(line, expectPrefix):
				res.Value = unquote(strings.TrimSpace(strings.TrimPrefix(line, expectPrefix)))
			case strings.HasPrefix(line, "+"):
				res.URCs = append(res.URCs, line)
				if c.opts.URC != nil {
					c.opts.URC(line)
				}
			default:
				res.URCs = append(res.URCs, line)
			}
		}
	}
}

func normalizeCGLA(cmd string) (newCmd string, handle string, getRespCLA string, isCGLA bool) {
	upper := strings.ToUpper(cmd)
	if !strings.HasPrefix(upper, "AT+CGLA=") {
		return cmd, "", "", false
	}
	rest := cmd[len("AT+CGLA="):]
	p1 := strings.IndexByte(rest, ',')
	if p1 < 0 {
		return cmd, "", "", false
	}
	handle = strings.TrimSpace(rest[:p1])
	rest2 := rest[p1+1:]
	p2 := strings.IndexByte(rest2, ',')
	if p2 < 0 {
		return cmd, "", "", false
	}
	apduHex := unquote(strings.TrimSpace(rest2[p2+1:]))
	byteLen := len(apduHex) / 2
	if byteLen > 5 {
		if lc, err := strconv.ParseInt(apduHex[8:10], 16, 32); err == nil {
			if byteLen == 5+int(lc) {
				apduHex += "00"
			}
		}
	}
	if len(apduHex) >= 2 {
		if claByte, err := strconv.ParseUint(apduHex[0:2], 16, 8); err == nil {
			getRespCLA = fmt.Sprintf("%02x", claByte&0x03)
		}
	}
	newCmd = fmt.Sprintf("AT+CGLA=%s,%d,\\"%s\\"", handle, len(apduHex), apduHex)
	return newCmd, handle, getRespCLA, true
}

func (c *Client) CommandWithTimeout(cmd string, expectPrefix string, timeout time.Duration) (*Result, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.closed {
		return nil, errors.New("atclient: client closed")
	}
	if c.invalid != nil {
		return nil, fmt.Errorf("%w: %v", ErrDesynchronized, c.invalid)
	}

	fixedCmd, handle, getRespCLA, isCGLA := normalizeCGLA(cmd)
	if !isCGLA {
		return c.execSingleCommand(cmd, expectPrefix, timeout)
	}

	res, err := c.execSingleCommand(fixedCmd, expectPrefix, timeout)
	if err != nil || res == nil {
		return res, err
	}

	var fullData strings.Builder
	curRes := res
	for {
		_, hexPart, ok := strings.Cut(curRes.Value, ",")
		if !ok {
			return curRes, nil
		}
		hexPart = unquote(strings.TrimSpace(hexPart))
		if len(hexPart) < 4 {
			return curRes, nil
		}
		sw1 := strings.ToUpper(hexPart[len(hexPart)-4 : len(hexPart)-2])
		sw2 := strings.ToUpper(hexPart[len(hexPart)-2:])
		fullData.WriteString(hexPart[:len(hexPart)-4])

		if sw1 == "61" && getRespCLA != "" {
			getRespAPDU := fmt.Sprintf("%sc00000%s", getRespCLA, strings.ToLower(sw2))
			nextCmd := fmt.Sprintf("AT+CGLA=%s,10,\\"%s\\"", handle, getRespAPDU)
			nextRes, nextErr := c.execSingleCommand(nextCmd, expectPrefix, timeout)
			if nextErr != nil {
				return nextRes, nextErr
			}
			nextRes.URCs = append(curRes.URCs, nextRes.URCs...)
			curRes = nextRes
		} else {
			fullData.WriteString(sw1)
			fullData.WriteString(sw2)
			combined := fullData.String()
			curRes.Value = fmt.Sprintf("%d,\\"%s\\"", len(combined), combined)
			return curRes, nil
		}
	}
}"""

assert old_method in src, "Blok CommandWithTimeout tidak cocok!"
if '"strconv"' not in src:
    src = src.replace('"strings"', '"strconv"\n\t"strings"', 1)
src = src.replace(old_method, new_method, 1)

with open(target, "w", encoding="utf-8") as f:
    f.write(src)

print("Patch ISO 7816-4 / Sierra EM7430 selesai diterapkan.")
