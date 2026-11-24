target = "third_party/lpac/driver/apdu/at_etsi.c"

with open(target, "r", encoding="utf-8") as f:
    src = f.read()

old_func = """static int apdu_interface_transmit(struct euicc_ctx *ctx, uint8_t **rx, uint32_t *rx_len, const uint8_t *tx,
                                   const uint32_t tx_len) {
    struct at_userdata *userdata = ctx->apdu.interface->userdata;
    const char *logic_channel = at_channel_get(userdata, ctx->apdu._internal.logic_channel);

    int fret = 0;
    char *response = NULL;

    int ret;
    char *encoded = NULL;

    *rx = NULL;
    *rx_len = 0;

    encoded = malloc(tx_len * 2 + 1);
    euicc_hexutil_bin2hex(encoded, tx_len * 2 + 1, tx, tx_len);

    at_emit_command(userdata, "AT+CGLA=%s,%u,\\"%s\\"", logic_channel, tx_len * 2, encoded);
    if (at_expect(userdata, &response, "+CGLA: ") != 0 || response == NULL)
        goto err;

    strtok(response, ",");        // Skip length
    response = strtok(NULL, ","); // Get response
    size_t n = strlen(response);
    if (response[0] == '"' && response[n - 1] == '"') {
        memmove(response, response + 1, n - 2);
        response[n - 2] = '\\0';
    }

    *rx_len = strlen(response) / 2;
    *rx = malloc(*rx_len);
    if (*rx == NULL)
        goto err;
    ret = euicc_hexutil_hex2bin_r(*rx, *rx_len, response, strlen(response));
    if (ret < 0)
        goto err;
    *rx_len = ret;

    goto exit;

err:
    fret = -1;
    free(*rx);
    *rx = NULL;
    *rx_len = 0;
exit:
    free(encoded);
    return fret;
}"""

new_func = """static int apdu_interface_transmit(struct euicc_ctx *ctx, uint8_t **rx, uint32_t *rx_len, const uint8_t *tx,
                                   const uint32_t tx_len) {
    struct at_userdata *userdata = ctx->apdu.interface->userdata;
    const char *logic_channel = at_channel_get(userdata, ctx->apdu._internal.logic_channel);

    int fret = 0;
    char *response = NULL;
    char *encoded = NULL;
    uint8_t *fixed_tx = NULL;
    uint8_t *total_buf = NULL;
    uint32_t total_len = 0;
    uint32_t total_cap = 4096;

    *rx = NULL;
    *rx_len = 0;

    fixed_tx = malloc(tx_len + 2);
    total_buf = malloc(total_cap);
    if (!fixed_tx || !total_buf)
        goto err;

    memcpy(fixed_tx, tx, tx_len);
    uint32_t fixed_len = tx_len;
    if (tx_len > 5 && tx_len == (uint32_t)(5 + tx[4])) {
        fixed_tx[fixed_len++] = 0x00;
    }

    uint8_t get_resp_cla = fixed_tx[0] & 0x03;
    uint8_t get_resp_cmd[5];
    const uint8_t *cur_tx = fixed_tx;
    uint32_t cur_tx_len = fixed_len;

    while (1) {
        free(encoded);
        encoded = malloc(cur_tx_len * 2 + 1);
        if (!encoded)
            goto err;
        euicc_hexutil_bin2hex(encoded, cur_tx_len * 2 + 1, cur_tx, cur_tx_len);

        free(response);
        response = NULL;
        at_emit_command(userdata, "AT+CGLA=%s,%u,\\"%s\\"", logic_channel, cur_tx_len * 2, encoded);
        if (at_expect(userdata, &response, "+CGLA: ") != 0 || response == NULL)
            goto err;

        strtok(response, ",");
        char *hex_part = strtok(NULL, ",");
        if (!hex_part)
            goto err;
        size_t n = strlen(hex_part);
        if (n >= 2 && hex_part[0] == '"' && hex_part[n - 1] == '"') {
            memmove(hex_part, hex_part + 1, n - 2);
            hex_part[n - 2] = '\\0';
        }

        uint32_t chunk_cap = (uint32_t)(strlen(hex_part) / 2);
        uint8_t *chunk_bin = malloc(chunk_cap + 1);
        if (!chunk_bin)
            goto err;
        int chunk_len = euicc_hexutil_hex2bin_r(chunk_bin, chunk_cap, hex_part, strlen(hex_part));
        if (chunk_len < 2) {
            free(chunk_bin);
            goto err;
        }

        uint8_t sw1 = chunk_bin[chunk_len - 2];
        uint8_t sw2 = chunk_bin[chunk_len - 1];
        uint32_t body_len = (uint32_t)(chunk_len - 2);

        if (total_len + body_len + 2 > total_cap) {
            total_cap = (total_len + body_len + 2) * 2;
            uint8_t *nb = realloc(total_buf, total_cap);
            if (!nb) {
                free(chunk_bin);
                goto err;
            }
            total_buf = nb;
        }

        memcpy(total_buf + total_len, chunk_bin, body_len);
        total_len += body_len;
        free(chunk_bin);

        if (sw1 == 0x61) {
            get_resp_cmd[0] = get_resp_cla;
            get_resp_cmd[1] = 0xC0;
            get_resp_cmd[2] = 0x00;
            get_resp_cmd[3] = 0x00;
            get_resp_cmd[4] = sw2;
            cur_tx = get_resp_cmd;
            cur_tx_len = 5;
        } else {
            total_buf[total_len++] = sw1;
            total_buf[total_len++] = sw2;
            break;
        }
    }

    *rx = total_buf;
    *rx_len = total_len;
    total_buf = NULL;
    goto exit;

err:
    fret = -1;
    free(total_buf);
    *rx = NULL;
    *rx_len = 0;
exit:
    free(response);
    free(encoded);
    free(fixed_tx);
    return fret;
}"""

assert old_func in src, "Blok apdu_interface_transmit tidak cocok!"
src = src.replace(old_func, new_func, 1)

with open(target, "w", encoding="utf-8") as f:
    f.write(src)

print("Sukses mem-patch third_party/lpac/driver/apdu/at_etsi.c untuk Sierra EM7430!")
