"""The line atlas holding dash patterns (512 rows; a round-cap pattern takes
15) grows to 4096 rows when full, then starts over, instead of returning no
pattern, which threw and stopped the whole map from drawing (a dash array
per zoom of a few layers fills it within a session).

Rows keep their place when the atlas grows (the shaders divide by the
current height). Starting over may drop the pattern looked up just before,
so the draw looks up the "from" side again after the "to" side.
"""

EDITS = [
    ("main", """
    addDash(dasharray, round) {
        const n = round ? 7 : 0;
        const height = 2 * n + 1;
        if (this.nextRow + height > this.height) {
            warnOnce("LineAtlas out of space");
            return null;
        }""", """
    q2grow() {
        if (this.height < 4096) {
            const data = new Uint8Array(this.width * this.height * 2);
            data.set(this.data);
            this.data = data;
            this.height *= 2;
        } else {
            this.data.fill(0);
            this.nextRow = 0;
            for (const key in this.dashEntry) delete this.dashEntry[key];
        }
        this.q2resize = true;
        this.dirty = true;
    }
    addDash(dasharray, round) {
        const n = round ? 7 : 0;
        const height = 2 * n + 1;
        if (this.nextRow + height > this.height) this.q2grow();
        if (this.nextRow + height > this.height) {
            warnOnce("LineAtlas out of space");
            return null;
        }""", 1),
    # A larger atlas is a new texture.
    ("main", """
    bind(context) {
        const gl = context.gl;
        if (!this.texture) {
            this.texture = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, this.texture);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);""", """
    bind(context) {
        const gl = context.gl;
        if (this.q2resize && this.texture) {
            gl.deleteTexture(this.texture);
            this.texture = null;
        }
        this.q2resize = false;
        if (!this.texture) {
            this.texture = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, this.texture);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);""", 1),
    ("main", """
            const dashTo = painter.lineAtlas.getDash(constantDasharray.to, round);
            const dashFrom = painter.lineAtlas.getDash(constantDasharray.from, round);""", """
            painter.lineAtlas.getDash(constantDasharray.from, round);
            const dashTo = painter.lineAtlas.getDash(constantDasharray.to, round);
            const dashFrom = painter.lineAtlas.getDash(constantDasharray.from, round);""", 1),
]
