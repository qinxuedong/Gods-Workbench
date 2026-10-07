/*!
 * Episode Liquid Metal — step-button renderer for the Gods' Workbench
 * episode pipeline step bar (#episodeStepNav).
 *
 * Vendored and adapted from the ThreeUI "Liquid Metal Button" (pill)
 * community component.
 *
 * Upstream source
 * ---------------
 *   Page:     https://threeui.com/buttons/liquid-metal-button/pill
 *   Repo:     https://github.com/MengTo/threeui
 *   Package:  @designcodeio/threeui v1.1.0 (npm; tarball shasum
 *             e7bd0a50df99e888882462997c62b5d00fbb4830)
 *   Upstream  lib-dist/shaders/liquid-metal-button/liquid-metal-button.html.js
 *   file:     SHA-256 5590d52936f92adf862cbd2c4f9fdeb3ec7c5063a772ac5fb8adfeb1aa226f4a
 *   Taken:    2026-08-29
 *   License:  MIT, Copyright (c) 2026 Meng To.  The complete MIT license text
 *             required by the upstream license is reproduced at the bottom of
 *             this header and must accompany any redistribution.
 *
 * Modifications over the upstream demo document (all local to this file)
 * -----------------------------------------------------------------------
 *  1. Parameterised the single-pill demo into N pills sharing ONE WebGL2
 *     canvas: uC/uHalf (and hover/press/pointer/ripple state) are per-button;
 *     each animating pill runs the same five-pass pipeline inside a scissor
 *     rect so concurrent pills cannot overwrite each other's glow.
 *  2. Static-frame mode: at rest the canvas renders exactly one frame and the
 *     requestAnimationFrame loop stops; hovered/pressed pills animate and the
 *     selected pill keeps a low-gain rim ("default silent except current
 *     step").  The upstream demo kept a travelling rim and a permanent rAF
 *     loop.
 *  3. Interaction state is scoped to per-pill instance objects.  The upstream
 *     document.body 'hot'/'press' classes and the window.__P/__E/__C/__R /
 *     __hover/__press/__ripple/__seek tuning hooks are removed.
 *  4. Removed the upstream Google Fonts CDN links, the demo page CSS
 *     and the demo's "WebGL2 is required" error fallback.  Degradation is
 *     fully silent: when WebGL2 (or shader compilation) is unavailable the
 *     canvas is never mounted and the existing CSS buttons stand unchanged.
 *  5. prefers-reduced-motion: the overlay is never mounted and no animation
 *     ever starts (the upstream demo still drew static frames).
 *  6. FRAG_RIM final colour is gated by hover/press activation plus a
 *     low-gain active-step signal (the "GW:" marked edits inside the vendored
 *     GLSL) so inactive resting pills are fully transparent and the default
 *     SIGNAL-FLOW button visuals (incl. mint/amber status colours) stay
 *     untouched.
 *  7. App integration API: EpisodeLiquidMetal.init/refresh/isAnimating/
 *     isMounted.  refresh() re-measures pill geometry after the step bar
 *     re-renders (activeStep/status changes) and repaints the one static
 *     frame; the overlay is hidden while the bar is empty.
 *  8. Keyboard-focus QA revision (R2): focusin and roving keyboard navigation
 *     light the focused pill with hover parity while pointer focus remains
 *     quiet.
 *  9. Selected-step QA revision (R3): the active pill breathes at 0.35 gain at
 *     rest; CSS step buttons use a matching capsule radius and omit numeric
 *     prefixes; inspect() exposes test-only state diagnostics.
 * 10. Coordinate mapping revision (v3.6): the CSS-clamped overlay and its
 *     backing store share the same rendered dimensions, and pill measurements
 *     use the actual CSS-to-device-pixel scale so glow centres remain aligned
 *     when the header clips the glow padding.
 *
 * MIT License
 *
 * Copyright (c) 2026 Meng To
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
(function (window, document) {
  'use strict';

  /* ====================================================================
   * Vendored shaders — threeui liquid-metal-button pill (MIT, see header).
   * Verbatim except the "GW:" marked activation edits in FRAG_RIM.
   * ==================================================================== */
  const VERT = `#version 300 es
in vec2 position; void main(){ gl_Position = vec4(position,0.,1.); }`;

  const HEAD = `#version 300 es
precision highp float;
out vec4 o;

uniform vec2  uC;        // pill centre, device px
uniform vec2  uHalf;     // pill half-extent, device px
uniform float uT;        // seconds
uniform float uHover;    // 0..1
uniform float uPress;    // 0..1, eased
uniform vec4  uRip[3];   // xy centre (button heights, +y down), z start, w live
uniform vec4  uRipK;     // speed, ring width, decay, amplitude
uniform vec4  uRipK2;    // facet depth, facet count, crest sharpness, emission
uniform vec4  uPtr;      // xy trailing cursor, z strength, w normalised speed
uniform vec4  uPtrK;     // radius, base amplitude, speed amplitude, rim lift

#define PI 3.14159265

float sdPill(vec2 p, vec2 b, float r){
  vec2 q = abs(p) - b + r;
  return min(max(q.x,q.y),0.) + length(max(q,0.)) - r;
}

/* Expanding ring from each press, in button-height units.  Three slots so a
   quick double-tap overlaps instead of cutting the first one off.

   Two things keep it from reading as a water ripple: the wavefront is
   faceted rather than circular — its radius is modulated by angle, and the
   facets rotate as it travels — and the crest profile is a cusp rather than
   a gaussian, so it lands as a crease in sheet metal instead of a soft swell. */
float ripple(vec2 p, float t){
  float sum = 0.;
  for(int i = 0; i < 3; i++){
    if(uRip[i].w < 0.5) continue;
    float age = t - uRip[i].z;
    if(age < 0. || age > 4.) continue;
    vec2  rp = p - uRip[i].xy;
    float facet = 1. + uRipK2.x * cos(uRipK2.y * atan(rp.y, rp.x) + age * 2.1 + float(i) * 2.4);
    float x = (length(rp) - age * uRipK.x * facet) / uRipK.y;
    sum += exp(-pow(abs(x) + 1e-4, uRipK2.z)) * exp(-age * uRipK.z);
  }
  return sum;
}

/* A soft well under the cursor.  It lags behind the real pointer and swells
   with speed, so moving across the button drags the metal rather than sliding
   a static blob over it. */
float pointerW(vec2 p){
  if(uPtr.z < 0.001) return 0.;
  float d = length(p - uPtr.xy) / uPtrK.x;
  return exp(-d*d) * uPtr.z;
}
/* Displacing the sample point, not the field value, is what makes this read as
   liquid: the bands bulge and stretch around the cursor like a lens instead of
   just getting brighter under it. */
vec2 pointerWarp(vec2 p){
  float w = pointerW(p);
  if(w <= 0.) return vec2(0.);
  return normalize(p - uPtr.xy + vec2(1e-5)) * w * (uPtrK.y + uPtrK.z * uPtr.w);
}
`;

  /* ---- the travelling rim, in its own pass so the blur below never touches it */
  const FRAG_RIM = HEAD + `
uniform float uBw;       // stroke half-width, device px
uniform float uActive;   // GW: 1 for the active (selected) step pill
uniform float uE[8];     // base, hot, chroma-across, chroma-along, speed,
                         // topBias, press lift, ripple lift

/* Arc-length position around the pill, 0..1, starting at the right-hand
   extreme and running counter-clockwise.  Straight runs and caps are measured
   in real length so a highlight travels at a constant speed all the way
   round instead of stalling on the caps. */
float perim(vec2 d, float a, float r){
  float P = 4.*a + 2.*PI*r;
  float s;
  if(d.x >= a){                                   // right cap
    float th = atan(d.y, d.x - a); if(th < 0.) th += 2.*PI;
    s = (th <= PI*0.5) ? r*th : P - r*(2.*PI - th);
  } else if(d.x <= -a){                           // left cap
    float th = atan(d.y, d.x + a); if(th < 0.) th += 2.*PI;
    s = r*PI*0.5 + 2.*a + r*(th - PI*0.5);
  } else if(d.y >= 0.){                           // top run
    s = r*PI*0.5 + (a - d.x);
  } else {                                        // bottom run
    s = r*PI*1.5 + 2.*a + (d.x + a);
  }
  return s / P;
}
// periodic bump, so a highlight wraps cleanly at s = 0
float pb(float u, float w){ u = fract(u); float x = min(u, 1.-u); return exp(-(x*x)/(w*w)); }

// travelling brightness around the rim — three lobes at different speeds and
// widths, which never quite re-align, so the light keeps re-pooling
float rimHot(float s, float t){
  float v = uE[0];
  v += 0.62 * pb(s - t*uE[4],             0.075);
  v += 0.44 * pb(s + t*uE[4]*0.63 + 0.41, 0.135);
  v += 0.30 * pb(s - t*uE[4]*0.34 + 0.73, 0.200);
  return v;
}
// soft band riding the pill edge, offset per channel to fringe across the stroke
float rimBand(float sd, float off){ return 1. - smoothstep(0., uBw*1.05, abs(sd + uBw*0.55 + off)); }

void main(){
  vec2  d  = gl_FragCoord.xy - uC;
  float sd = sdPill(d, uHalf, uHalf.y);
  if(sd > uBw*2.5 || sd < -uBw*3.5){ o = vec4(0.); return; }

  /* Each channel is offset both *across* the stroke and *along* it, so the rim
     fringes red-outside / cyan-inside and its hue also drifts as a highlight
     slides past — the two together are what read as metal rather than as a
     moving white dot. */
  float a = max(uHalf.x - uHalf.y, 0.);
  float s = perim(d, a, uHalf.y);
  float top = mix(1., 0.5 + 0.5 * (d.y / uHalf.y), uE[5]);

  // pressing lifts the whole outline, and each ripple flares it again as the
  // ring sweeps past — so the rim reports the press twice, once as a step and
  // once as a wave running round the edge
  // …and the stretch of outline nearest the cursor picks up a little too
  vec2  p   = vec2(d.x, -d.y) / (uHalf.y * 2.);
  float lift = 1. + uPress * uE[6] + ripple(p, uT) * uE[7]
             + pointerW(p) * uPtrK.w;

  // GW: activation gate — upstream kept the rim travelling (and visible) at
  // rest; here the rim exists while the pill is hovered/pressed, and the
  // ACTIVE (selected) step keeps a low-gain travelling rim at rest (0.35 of
  // full activation, requirement 25 revision 3) so inactive pills stay fully
  // transparent at rest and the default CSS visuals stand.
  float act = clamp(max(max(uHover, uPress), uActive * 0.35), 0., 1.);
  o = vec4(vec3(
    rimBand(sd,  uE[2]) * rimHot(s + uE[3], uT),
    rimBand(sd,  0.   ) * rimHot(s,         uT),
    rimBand(sd, -uE[2]) * rimHot(s - uE[3], uT)
  ) * uE[1] * top * lift * act, 1.);
}`;

  const FRAG_SCENE = HEAD + `
uniform float uP[21];    // tunables

float h21(vec2 p){
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float vn(vec2 p){
  vec2 i = floor(p), f = fract(p);
  f = f*f*(3.-2.*f);
  float a = h21(i), b = h21(i+vec2(1,0)), c = h21(i+vec2(0,1)), d = h21(i+vec2(1,1));
  return mix(mix(a,b,f.x), mix(c,d,f.x), f.y) * 2. - 1.;
}
// normalised to roughly -1..1; low gain keeps the first octave dominant, which
// is what keeps the ribbons big and smooth instead of turbulent
float fbm(vec2 p, float g){
  float s = 0., a = 1., n = 0.;
  for(int i=0;i<4;i++){ s += a*vn(p); n += a; p = p*2.03 + 11.7; a *= g; }
  return s / n;
}
float fbm(vec2 p){ return fbm(p, 0.5); }

/* p is in button-height units, +y down, origin at the pill centre.

   The bands in the reference are a *family of parallel curves*: one swooping
   valley repeated up the button, dense where the light is pinched and pulled
   wide open where it is not.  So the field is built that way explicitly —

       V = (y - valley(x)) * density(x)

   — rather than hoping 2-D noise happens to produce it.  Level sets of V are
   all vertical translates of the same valley curve, which is what makes the
   ribbons laminar and near-parallel; a density that varies along x is what makes
   them crowd into razor fringes at one end and open into a broad wash at the
   other.  A soft plateau over V then paints them, sampled once per
   wavelength at slightly offset heights, so every edge opens into a prism of
   width dispersion / |grad V|.                                             */

// smooth 1-D wiggle that drifts slowly with time
float wig(float x, float t, float seed){
  return vn(vec2(x,          t*0.150 + seed)) * 0.60
       + vn(vec2(x*2.07 + 4., t*0.105 + seed)) * 0.27
       + vn(vec2(x*4.30 - 7., t*0.080 + seed)) * 0.13;
}

float valleyAt(vec2 p, float t){ return wig(p.x*uP[0], t, 0.0) * uP[1]; }
float densAt  (vec2 p, float t){ return uP[2] * exp(uP[3] * wig(p.x*uP[4] + 9.0, t, 2.7)); }

float surface(vec2 p, float t){
  float V = (p.y - valleyAt(p,t)) * densAt(p,t);
  V += uP[5] * fbm(p*vec2(0.8, 1.7)*uP[6] + vec2(t*0.05, -t*0.03), uP[17]);
  return V - uP[7];
}
// One plateau per unit of V — so the density is literally bands per button height.
// A plateau rather than a step is what puts warm on the low edge and cool on
// the high edge of every ribbon.
float tone(float v){
  float u = fract(v);
  float e = uP[9], W = uP[10] * 0.5;
  return smoothstep(0.5-W-e, 0.5-W, u) * (1. - smoothstep(0.5+W, 0.5+W+e, u));
}
vec3 spec(float t){ return clamp(vec3(1.5) - abs(4.*t - vec3(3.,2.,1.)), 0., 1.); }

void main(){
  vec2  d  = gl_FragCoord.xy - uC;
  float sd = sdPill(d, uHalf, uHalf.y);
  float pill = 1. - smoothstep(-1., 1., sd);
  float S = uHalf.y * 2.;                 // button height, device px
  float t = uT;

  // rgb is premultiplied by the mask and alpha carries it, so the blur that
  // follows can normalise and keep a clean edge instead of a dark vignette
  if(uHover <= 0.0015 || pill <= 0.0015){ o = vec4(0., 0., 0., pill); return; }

  vec2  p = vec2(d.x, -d.y) / S;          // gl_FragCoord is y-up
  vec2  q = p + pointerWarp(p);           // the cursor drags the sheet

  // self-refraction: bend the lookup along the field's own slope, which piles
  // iso-lines up into folds instead of leaving them evenly spaced
  float h0 = surface(q, t);
  vec2  gp = vec2(dFdx(h0), -dFdy(h0)) * S;          // grad in p-units
  float V  = surface(q - gp * uP[8] / max(uP[2], .001), t);

  // gradient-aligned filaments: fast variation across the iso-lines, slow
  // along them, so the fine detail reads as drawn-out fibres of light
  vec2  gd = normalize(gp + vec2(1e-5));
  V += uP[13] * fbm(vec2(dot(q,gd)*uP[14], dot(q, vec2(-gd.y,gd.x))*uP[14]*0.04) + vec2(0., t*0.06));

  // press ripple: displacing the field rather than adding light means the
  // bands themselves bow outwards as the ring passes, which is what sells it
  // as a disturbance *in* the metal instead of a decal over it
  float rip  = ripple(p, t);
  float well = pointerW(p);
  V += rip * uRipK.w;

  // Real dispersion is not linear in wavelength — the blue end bends far more
  // than the red (Cauchy).  Skewing the sample offsets the same way is what
  // gives the reference its broad cool wash against a tight warm edge.
  const int N = 21;
  float mid = 1. - pow(0.5, uP[12]);
  vec3 col = vec3(0.), wsum = vec3(0.);
  for(int i=0;i<N;i++){
    float k = float(i)/float(N-1);
    vec3  w = spec(k);
    col  += w * tone(V + ((1. - pow(1. - k, uP[12])) - mid) * uP[11]);
    wsum += w;
  }
  col /= wsum;
  col = pow(col, vec3(uP[15]));

  // light envelope — the ribbons only exist where the sheet is lit, and the
  // dark upper region is bounded by the same valley curve the bands follow
  float lit = smoothstep(uP[18], uP[19], q.y - valleyAt(q, t));
  lit *= mix(1., lit, 0.55);                     // deepen the unlit crescent
  col *= uP[16] * lit;

  // the crest runs hotter, and carries a little light of its own so it stays
  // legible through the softening blur and across the unlit part of the pill
  col = col * (1. + rip * 1.15 + well * 0.60);

  o = vec4(col * pill * uHover, pill);
}`;

  /* Downsample; optionally adding a second source (used to fold the rim into
     the bloom input).  Alpha rides along so the metal's coverage mask survives
     the blur chain. */
  const FRAG_DOWN = `#version 300 es
precision highp float;
out vec4 o;
uniform sampler2D uTex, uTex2;
uniform vec2 uDstTexel;   // 1 / destination size  (maps dest fragCoord -> uv)
uniform vec2 uSrcTexel;   // 1 / source size       (tap spacing)
uniform float uAdd;       // 1 to include uTex2
void main(){
  vec2 uv = gl_FragCoord.xy * uDstTexel;
  // Taps sit a quarter of a *destination* texel out, so for a 2x reduction
  // they land exactly on the four source texel centres.  Spacing them by a
  // whole source texel instead — as this did originally — skips every other
  // pixel, and any fine detail in the field folds down into low-frequency
  // moiré that no amount of subsequent blurring can remove.
  vec2 e = uDstTexel * 0.25;
  vec4 s = texture(uTex, uv + vec2(-e.x,-e.y)) + texture(uTex, uv + vec2( e.x,-e.y))
         + texture(uTex, uv + vec2(-e.x, e.y)) + texture(uTex, uv + vec2( e.x, e.y));
  s *= 0.25;
  if(uAdd > 0.5){
    vec4 r = texture(uTex2, uv + vec2(-e.x,-e.y)) + texture(uTex2, uv + vec2( e.x,-e.y))
           + texture(uTex2, uv + vec2(-e.x, e.y)) + texture(uTex2, uv + vec2( e.x, e.y));
    s.rgb += r.rgb * 0.25;
  }
  o = s;
}`;

  const FRAG_BLUR = `#version 300 es
precision highp float;
out vec4 o;
uniform sampler2D uTex; uniform vec2 uTexel; uniform vec2 uDir; uniform float uR;
void main(){
  vec2 uv = gl_FragCoord.xy * uTexel;
  vec2 st = uTexel * uDir * uR;
  vec4 s = texture(uTex, uv) * 0.1964;
  s += (texture(uTex, uv + st*1.4118) + texture(uTex, uv - st*1.4118)) * 0.2969;
  s += (texture(uTex, uv + st*3.2941) + texture(uTex, uv - st*3.2941)) * 0.0944;
  s += (texture(uTex, uv + st*5.1765) + texture(uTex, uv - st*5.1765)) * 0.0104;
  o = s;
}`;

  const FRAG_COMP = HEAD + `
uniform sampler2D uSoft, uRim, uGlow;
uniform vec2  uRes;
uniform float uGlowGain, uGlowIn, uOccl, uDim, uPunch;

void main(){
  vec2 uv = gl_FragCoord.xy / uRes;
  vec3 glow = texture(uGlow, uv).rgb;

  vec2  d    = gl_FragCoord.xy - uC;
  float sd   = sdPill(d, uHalf, uHalf.y);
  float pill = 1. - smoothstep(-1., 1., sd);

  // normalised blur: dividing by the blurred coverage keeps the softened metal
  // full strength right up to the edge instead of fading into the mask
  vec4 m = texture(uSoft, uv);

  // Scrim, applied *after* the blur: knock the metal back through the middle
  // where the label sits, leaving the top and bottom at full brightness.  Doing
  // this before the blur would smear the protection away at high blur values.
  float veil = 1. - smoothstep(0.46, 0.88, abs(d.y) / uHalf.y);

  // Blurring flattens the tonal range into a wash; putting the contrast back
  // with a power curve — after the blur, so it costs no smoothness — is what
  // makes it read as poured metal rather than a soft glow.  Highlights keep
  // their level while the mid-tones drop away.
  vec3 metal = pow(max(m.rgb / max(m.a, 1e-3), 0.), vec3(uPunch));

  vec3 core = metal * pill * mix(1., uDim, veil) + texture(uRim, uv).rgb;

  // The ripple's own light is added here, after the blur, so the crease stays
  // a hard line.  Its displacement of the field still rides inside the
  // softened metal — the sheet bows, and the crest glints along the fold.
  float rip = ripple(vec2(d.x, -d.y) / (uHalf.y * 2.), uT);
  core += vec3(rip * rip) * uRipK2.w * pill * mix(1., 0.42, veil);

  // The button occludes its own bloom over the patch where its shadow falls,
  // so the drop shadow keeps its contrast even when the face is blown out.
  float sdSh = sdPill(d + vec2(0., uHalf.y * 0.62), uHalf * 0.94, uHalf.y * 0.94);
  float occl = uOccl * exp(-max(sdSh, 0.) / (uHalf.y * 0.75));

  // Bloom spills mostly outward; a little of it is allowed back inside so the
  // hot rim bleeds onto the face, as it does on the reference component.
  vec3 rgb = core + glow * uGlowGain * mix(1., uGlowIn, pill) * (1. - occl * (1. - pill));

  // premultiplied — the page's ambient pool and the button's drop shadow are
  // CSS underneath, and this layer adds light on top of them
  float a = clamp(max(rgb.r, max(rgb.g, rgb.b)), 0., 1.);
  o = vec4(min(rgb, vec3(1.)), a);
}`;

  /* ====================================================================
   * Vendored tunables — threeui liquid-metal-button pill (MIT, see header).
   * The window.__P/__E/__C/__R exposures are removed (GW modification 3).
   * ==================================================================== */
  const P = {
    valFreq:   0.50, valAmp:    0.55, dens:      2.40, densVar:   2.20,
    densFreq:  0.32, wobAmp:    0.12, wobFreq:   1.60, lift:      0.05,
    refract:   0.18, edge:      0.04, width:     0.46, disp:      0.30,
    skew:      1.50, fineAmp:   0.0,  fineFreq:  9.0,  gamma:     1.00,
    gain:      1.90, octGain:   0.32, litLo:    -0.26, litHi:     0.10,
    dim:       0.44
  };
  const PKEYS = Object.keys(P);
  const E = {
    base:   0.20, hot:    0.82, chromA: 0.42, chromS: 0.030,
    speed:  0.070, top:   0.35, press:  0.85, ripple: 1.60
  };
  const EKEYS = Object.keys(E);
  const C = {
    glow:   1.95, glowR:  1.30, glowIn: 0.30, occl:   0.62,
    soften: 0.24, punch:  1.50
  };
  const R = {
    speed:  1.85, width:  0.20, decay:  1.35, amp:    1.35,
    facet:  0.18, lobes:  6.0,  sharp:  1.15, emit:   0.45,
    ptrRad:  0.55, ptrAmp: 0.32, ptrFast: 0.40, ptrRim:  0.80,
    ptrLag:  0.0016, ptrVref: 4.5
  };

  const GLOW_TEX = 129;        // demo bloom reference height (device px)
  const GLOW_PAD_CSS = 56;     // GW: css px of overlay margin around the bar

  let state = null;
  let dead = false;            // GW: permanent silent fallback (reduced motion
                               // or no WebGL2) — never retried, never reported

  function collectButtons(st) {
    return Array.prototype.slice.call(st.nav.querySelectorAll('.workflow-step'));
  }

  function init(nav) {
    if (state) return state;
    if (dead || !nav || !window.matchMedia) return null;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { dead = true; return null; }
    const header = nav.parentElement;
    if (!header) return null;

    const canvas = document.createElement('canvas');
    canvas.className = 'episode-liquid-canvas';
    canvas.setAttribute('aria-hidden', 'true');

    let gl = null;
    try {
      gl = canvas.getContext('webgl2', { alpha: true, antialias: false, premultipliedAlpha: true, powerPreference: 'high-performance' });
    } catch (_) { gl = null; }
    if (!gl) { dead = true; return null; } // GW: silent fallback, canvas never mounted

    function sh(type, src) {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    function prog(fs) {
      const p = gl.createProgram();
      gl.attachShader(p, sh(gl.VERTEX_SHADER, VERT));
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
      gl.bindAttribLocation(p, 0, 'position');
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      const u = {};
      const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); u[info.name.replace('[0]', '')] = gl.getUniformLocation(p, info.name); }
      return { p, u };
    }
    let pScene, pRim, pDown, pBlur, pComp;
    try {
      pScene = prog(FRAG_SCENE); pRim = prog(FRAG_RIM);
      pDown = prog(FRAG_DOWN); pBlur = prog(FRAG_BLUR); pComp = prog(FRAG_COMP);
    } catch (_) { dead = true; return null; } // GW: silent fallback

    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const vbo = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    const hasFloat = !!gl.getExtension('EXT_color_buffer_half_float');
    function makeTarget() {
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      return { tex, fbo, w: 0, h: 0 };
    }
    function sizeTarget(t, w, h) {
      if (t.w === w && t.h === h) return;
      t.w = w; t.h = h;
      gl.bindTexture(gl.TEXTURE_2D, t.tex);
      if (hasFloat) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
      else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    }
    const T_core = makeTarget(), T_rim = makeTarget(),   // full res
          T_s1   = makeTarget(), T_s2  = makeTarget(),   // half res: metal softening
          T_a    = makeTarget(), T_b   = makeTarget();   // 1/DOWN: bloom

    const st = {
      nav, header, canvas, buttons: [],
      W: 0, H: 0, DPR: 1, DOWN: 4, needMeasure: true,
      loopActive: false, clock: 0, last: 0, rafPending: 0,
      pills: [],
      repaint: null,
      keyboardNav: false
    };
    state = st;
    header.appendChild(canvas);

    function drawTo(t) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fbo : null);
      gl.viewport(0, 0, t ? t.w : st.W, t ? t.h : st.H);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    function measure() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const navRect = st.nav.getBoundingClientRect();
      const headRect = st.header.getBoundingClientRect();
      const hidden = !st.buttons.length || navRect.width < 4 || navRect.height < 4;
      canvas.style.display = hidden ? 'none' : '';
      if (hidden) { st.pills = []; st.W = 0; st.H = 0; st.needMeasure = false; return; }
      const requestedW = Math.max(2, Math.ceil(navRect.width + GLOW_PAD_CSS * 2));
      const requestedH = Math.max(2, Math.ceil(navRect.height + GLOW_PAD_CSS * 2));
      // GW: clamp the canvas rect to the header box so the glow margin can
      // never extend the document scroll area when the bar reaches the
      // viewport edge (requirement 25 v3.1 layout).
      const maxLeft = Math.max(0, headRect.width - 2);
      const left = Math.min(Math.max(navRect.left - headRect.left - GLOW_PAD_CSS, 0), maxLeft);
      const width = Math.max(2, Math.min(navRect.left - headRect.left - GLOW_PAD_CSS + requestedW, headRect.width) - left);
      // The browser scales a canvas when its CSS box and backing store have
      // different aspect scales. Keep both dimensions derived from the same
      // clamped box so shader coordinates and DOM coordinates share one map.
      const renderW = Math.max(2, Math.round(width));
      const renderH = Math.max(2, Math.round(requestedH));
      canvas.style.left = Math.round(left) + 'px';
      canvas.style.top = Math.round(Math.max(navRect.top - headRect.top - GLOW_PAD_CSS, 0)) + 'px';
      canvas.style.width = renderW + 'px';
      canvas.style.height = renderH + 'px';
      const w = Math.max(2, Math.round(renderW * dpr));
      const h = Math.max(2, Math.round(renderH * dpr));
      if (w !== st.W || h !== st.H || dpr !== st.DPR) { st.W = w; st.H = h; st.DPR = dpr; canvas.width = st.W; canvas.height = st.H; }
      const canvasRect = canvas.getBoundingClientRect();
      const scaleX = st.W / Math.max(canvasRect.width, 1);
      const scaleY = st.H / Math.max(canvasRect.height, 1);
      let maxBH = 1;
      st.pills = st.buttons.map(el => {
        const r = el.getBoundingClientRect();
        const bw = r.width * scaleX;
        const bh = Math.max(2, r.height * scaleY);
        const cx = (r.left - canvasRect.left) * scaleX + bw / 2;
        const cy = st.H - ((r.top - canvasRect.top) * scaleY + bh / 2); // gl_FragCoord is y-up
        maxBH = Math.max(maxBH, bh);
        return { el, cx, cy, hw: bw / 2, hh: bh / 2,
          active: el.classList.contains('is-active'),
          hover: 0, hoverTarget: 0, press: 0, pressTarget: 0,
          on: { over: false, press: false, focus: false },
          ptr: { x: 0, y: 0 }, ptrS: { x: 0, y: 0 }, ptrAmt: 0, ptrSpeed: 0,
          rip: [0, 1, 2].map(() => ({ x: 0, y: 0, t: -99, on: 0 })), ripNext: 0 };
      });
      st.DOWN = Math.max(1, Math.min(4, Math.round(maxBH / GLOW_TEX)));
      sizeTarget(T_core, st.W, st.H); sizeTarget(T_rim, st.W, st.H);
      const hw = Math.max(2, Math.ceil(st.W / 2)), hh = Math.max(2, Math.ceil(st.H / 2));
      sizeTarget(T_s1, hw, hh); sizeTarget(T_s2, hw, hh);
      const dw = Math.max(2, Math.ceil(st.W / st.DOWN)), dh = Math.max(2, Math.ceil(st.H / st.DOWN));
      sizeTarget(T_a, dw, dh); sizeTarget(T_b, dw, dh);
      st.needMeasure = false;
    }

    function pillEnergy(pill) {
      const ripLive = pill.rip.some(r => r.on);
      // GW (需求25 R3): the active step breathes at rest, so it always
      // carries energy while it stays selected.
      return Math.max(pill.hover, pill.press, pill.ptrAmt, pill.active ? 1 : 0, ripLive ? 1 : 0);
    }

    const uArr = new Float32Array(PKEYS.length);
    const eArr = new Float32Array(EKEYS.length);
    const ripArr = new Float32Array(12);

    /* GW: per-pill pipeline pass — upstream drew the one pill over the whole
       canvas; here uC/uHalf and the pill's own state are set per button and a
       scissor rect keeps concurrent pills from overwriting each other. */
    function drawPill(pill) {
      const pad = pill.hh * 5;
      const x0 = Math.max(0, Math.floor(pill.cx - pill.hw - pad));
      const x1 = Math.min(st.W, Math.ceil(pill.cx + pill.hw + pad));
      const yTop = Math.max(0, Math.floor(st.H - (pill.cy + pill.hh + pad)));
      const yBot = Math.min(st.H, Math.ceil(st.H - (pill.cy - pill.hh - pad)));
      if (x1 <= x0 || yBot <= yTop) return;
      gl.enable(gl.SCISSOR_TEST);
      gl.scissor(x0, st.H - yBot, x1 - x0, yBot - yTop);

      for (let i = 0; i < uArr.length; i++) uArr[i] = P[PKEYS[i]];
      for (let i = 0; i < eArr.length; i++) eArr[i] = E[EKEYS[i]];
      for (let i = 0; i < 3; i++) {
        const r = pill.rip[i];
        ripArr[i * 4] = r.x; ripArr[i * 4 + 1] = r.y; ripArr[i * 4 + 2] = r.t; ripArr[i * 4 + 3] = r.on;
      }
      const bw = Math.max(1.5, 3.2 * ((pill.hh * 2) / 516));
      const BH = pill.hh * 2;

      // 1. metal, masked to the pill
      gl.useProgram(pScene.p);
      gl.uniform2f(pScene.u.uC, pill.cx, pill.cy);
      gl.uniform2f(pScene.u.uHalf, pill.hw, pill.hh);
      gl.uniform1f(pScene.u.uT, st.clock);
      gl.uniform1f(pScene.u.uHover, pill.hover);
      gl.uniform1f(pScene.u.uPress, pill.press);
      gl.uniform4fv(pScene.u.uRip, ripArr);
      gl.uniform4f(pScene.u.uRipK, R.speed, R.width, R.decay, R.amp);
      gl.uniform4f(pScene.u.uRipK2, R.facet, R.lobes, R.sharp, R.emit);
      gl.uniform4f(pScene.u.uPtr, pill.ptrS.x, pill.ptrS.y, pill.ptrAmt, pill.ptrSpeed);
      gl.uniform4f(pScene.u.uPtrK, R.ptrRad, R.ptrAmp, R.ptrFast, R.ptrRim);
      gl.uniform1fv(pScene.u.uP, uArr);
      drawTo(T_core);

      // 2. rim
      gl.useProgram(pRim.p);
      gl.uniform2f(pRim.u.uC, pill.cx, pill.cy);
      gl.uniform2f(pRim.u.uHalf, pill.hw, pill.hh);
      gl.uniform1f(pRim.u.uT, st.clock);
      gl.uniform1f(pRim.u.uBw, bw);
      gl.uniform1f(pRim.u.uActive, pill.active ? 1 : 0);
      gl.uniform1f(pRim.u.uPress, pill.press);
      gl.uniform4fv(pRim.u.uRip, ripArr);
      gl.uniform4f(pRim.u.uRipK, R.speed, R.width, R.decay, R.amp);
      gl.uniform4f(pRim.u.uRipK2, R.facet, R.lobes, R.sharp, R.emit);
      gl.uniform4f(pRim.u.uPtr, pill.ptrS.x, pill.ptrS.y, pill.ptrAmt, pill.ptrSpeed);
      gl.uniform4f(pRim.u.uPtrK, R.ptrRad, R.ptrAmp, R.ptrFast, R.ptrRim);
      gl.uniform1fv(pRim.u.uE, eArr);
      drawTo(T_rim);

      // 3. soften the metal
      gl.useProgram(pDown.p);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, T_core.tex);
      gl.uniform1i(pDown.u.uTex, 0);
      gl.uniform1f(pDown.u.uAdd, 0);
      gl.uniform2f(pDown.u.uDstTexel, 1 / T_s1.w, 1 / T_s1.h);
      gl.uniform2f(pDown.u.uSrcTexel, 1 / st.W, 1 / st.H);
      drawTo(T_s1);

      gl.useProgram(pBlur.p);
      gl.uniform1i(pBlur.u.uTex, 0);
      gl.uniform2f(pBlur.u.uTexel, 1 / T_s1.w, 1 / T_s1.h);
      const sigTex = C.soften * (BH * 0.5) * 0.95;
      if (sigTex > 0.1) {
        const iters = Math.min(4, Math.max(1, Math.ceil(sigTex / 3.0)));
        gl.uniform1f(pBlur.u.uR, sigTex / Math.sqrt(iters) / 1.95);
        for (let i = 0; i < iters; i++) {
          gl.bindTexture(gl.TEXTURE_2D, T_s1.tex); gl.uniform2f(pBlur.u.uDir, 1, 0); drawTo(T_s2);
          gl.bindTexture(gl.TEXTURE_2D, T_s2.tex); gl.uniform2f(pBlur.u.uDir, 0, 1); drawTo(T_s1);
        }
      }

      // 4. bloom, fed by the softened metal plus the crisp rim
      gl.useProgram(pDown.p);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, T_s1.tex);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, T_rim.tex);
      gl.uniform1i(pDown.u.uTex, 0);
      gl.uniform1i(pDown.u.uTex2, 1);
      gl.uniform1f(pDown.u.uAdd, 1);
      gl.uniform2f(pDown.u.uDstTexel, 1 / T_a.w, 1 / T_a.h);
      gl.uniform2f(pDown.u.uSrcTexel, 1 / T_s1.w, 1 / T_s1.h);
      drawTo(T_a);

      gl.useProgram(pBlur.p);
      gl.activeTexture(gl.TEXTURE0);
      gl.uniform1i(pBlur.u.uTex, 0);
      gl.uniform2f(pBlur.u.uTexel, 1 / T_a.w, 1 / T_a.h);
      const rs = C.glowR * (BH / st.DOWN) / GLOW_TEX;
      for (const r of [1.0, 2.3, 5.2, 9.0].map(v => v * rs)) {
        gl.uniform1f(pBlur.u.uR, r);
        gl.bindTexture(gl.TEXTURE_2D, T_a.tex); gl.uniform2f(pBlur.u.uDir, 1, 0); drawTo(T_b);
        gl.bindTexture(gl.TEXTURE_2D, T_b.tex); gl.uniform2f(pBlur.u.uDir, 0, 1); drawTo(T_a);
      }

      // 5. composite
      gl.useProgram(pComp.p);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, T_s1.tex);  gl.uniform1i(pComp.u.uSoft, 0);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, T_rim.tex); gl.uniform1i(pComp.u.uRim, 1);
      gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, T_a.tex);   gl.uniform1i(pComp.u.uGlow, 2);
      gl.uniform2f(pComp.u.uRes, st.W, st.H);
      gl.uniform2f(pComp.u.uC, pill.cx, pill.cy);
      gl.uniform2f(pComp.u.uHalf, pill.hw, pill.hh);
      gl.uniform1f(pComp.u.uT, st.clock);
      gl.uniform4fv(pComp.u.uRip, ripArr);
      gl.uniform4f(pComp.u.uRipK, R.speed, R.width, R.decay, R.amp);
      gl.uniform4f(pComp.u.uRipK2, R.facet, R.lobes, R.sharp, R.emit);
      gl.uniform1f(pComp.u.uGlowGain, C.glow);
      gl.uniform1f(pComp.u.uGlowIn, C.glowIn);
      gl.uniform1f(pComp.u.uOccl, C.occl);
      gl.uniform1f(pComp.u.uDim, P.dim);
      gl.uniform1f(pComp.u.uPunch, C.punch);
      drawTo(null);
      gl.disable(gl.SCISSOR_TEST);
    }

    function clearCanvas() {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, st.W, st.H);
      gl.disable(gl.SCISSOR_TEST);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
    }

    /* GW: static frame — the resting canvas is one transparent frame
       (upstream kept drawing every frame). */
    function renderStatic() {
      if (st.needMeasure) measure();
      if (!st.pills.length || st.W < 2 || st.H < 2) return;
      clearCanvas();
      for (const pill of st.pills) if (pillEnergy(pill) > 0.002) drawPill(pill);
    }

    function pillFor(target) {
      const el = target && target.closest ? target.closest('.workflow-step') : null;
      if (!el) return null;
      return st.pills.find(pill => pill.el === el) || null;
    }
    function localPt(e, pill) {
      const b = pill.el.getBoundingClientRect(), s = b.height;
      return [(e.clientX - (b.left + b.width / 2)) / s,
              (e.clientY - (b.top + b.height / 2)) / s];
    }
    function addRipple(pill, x, y) {
      const r = pill.rip[pill.ripNext];
      pill.ripNext = (pill.ripNext + 1) % pill.rip.length;
      r.x = x; r.y = y; r.t = st.clock; r.on = 1;
    }

    function frame(now) {
      st.rafPending = 0;
      if (!st.loopActive) return;
      const dt = Math.min(Math.max((now - st.last) / 1000, 0), 1 / 20);
      st.last = now;
      st.clock += dt;
      let any = false;
      for (const pill of st.pills) {
        // asymmetric ease: quick to bloom, a touch quicker to die
        const hk = pill.hoverTarget > pill.hover ? 1 - Math.pow(0.0012, dt) : 1 - Math.pow(0.00012, dt);
        pill.hover += (pill.hoverTarget - pill.hover) * hk;
        if (Math.abs(pill.hoverTarget - pill.hover) < 0.0008) pill.hover = pill.hoverTarget;
        // press snaps on and lets go slowly
        const pk = pill.pressTarget > pill.press ? 1 - Math.pow(1e-9, dt) : 1 - Math.pow(0.004, dt);
        pill.press += (pill.pressTarget - pill.press) * pk;
        if (Math.abs(pill.pressTarget - pill.press) < 0.002) pill.press = pill.pressTarget;
        for (const r of pill.rip) if (r.on && st.clock - r.t > 4) r.on = 0;
        // the well trails the cursor and swells with how fast it is being dragged
        const lag = 1 - Math.pow(R.ptrLag, dt);
        const dx = (pill.ptr.x - pill.ptrS.x) * lag, dy = (pill.ptr.y - pill.ptrS.y) * lag;
        pill.ptrS.x += dx; pill.ptrS.y += dy;
        const inst = Math.min(Math.hypot(dx, dy) / Math.max(dt, 1e-3) / R.ptrVref, 1);
        pill.ptrSpeed += (inst - pill.ptrSpeed) * (1 - Math.pow(inst > pill.ptrSpeed ? 0.001 : 0.02, dt));
        const wantWell = (pill.on.over || pill.on.press) ? 1 : 0;
        pill.ptrAmt += (wantWell - pill.ptrAmt) * (1 - Math.pow(0.004, dt));
        if (Math.abs(wantWell - pill.ptrAmt) < 0.002) pill.ptrAmt = wantWell;
        if (pillEnergy(pill) > 0.002 || pill.hover !== pill.hoverTarget || pill.press !== pill.pressTarget) any = true;
      }
      if (st.needMeasure) measure();
      if (!st.pills.length || !any) {
        renderStatic();          // GW: settle into the one static frame, then stop
        st.loopActive = false;
        return;
      }
      clearCanvas();
      for (const pill of st.pills) if (pillEnergy(pill) > 0.002) drawPill(pill);
      st.rafPending = window.requestAnimationFrame(now2 => frame(now2));
    }

    /* GW: upstream sync() equivalent — hover/press/focus flags map onto the
       easing targets, then the loop starts (and settles back to the static
       frame once every pill is at rest). */
    function wake() {
      for (const pill of st.pills) {
        pill.hoverTarget = (pill.on.over || pill.on.press || pill.on.focus) ? 1 : 0;
        pill.pressTarget = pill.on.press ? 1 : 0;
      }
      if (st.loopActive) return;
      st.loopActive = true;
      st.last = window.performance.now();
      if (!st.rafPending) st.rafPending = window.requestAnimationFrame(now => frame(now));
    }

    /* GW: delegated on the step bar so bar re-renders keep the wiring */
    nav.addEventListener('pointerover', e => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      const pill = pillFor(e.target); if (!pill) return;
      const pt = localPt(e, pill);
      pill.ptr.x = pt[0]; pill.ptr.y = pt[1]; pill.ptrS.x = pt[0]; pill.ptrS.y = pt[1]; pill.ptrSpeed = 0;
      pill.on.over = true; wake();
    });
    nav.addEventListener('pointerout', e => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      const pill = pillFor(e.target); if (!pill) return;
      pill.on.over = false; wake();
    });
    // tracked on the window so a press can slide off the button, but only
    // measured while a pill is actually engaged
    window.addEventListener('pointermove', e => {
      for (const pill of st.pills) {
        if (!pill.on.over && !pill.on.press) continue;
        const pt = localPt(e, pill);
        pill.ptr.x = pt[0]; pill.ptr.y = pt[1];
      }
    }, { passive: true });
    nav.addEventListener('pointerdown', e => {
      st.keyboardNav = false; // GW: pointer modality ends the keyboard-focus lighting window
      const pill = pillFor(e.target); if (!pill) return;
      const pt = localPt(e, pill);
      pill.ptr.x = pt[0]; pill.ptr.y = pt[1];
      pill.on.press = true;
      addRipple(pill, pt[0], pt[1]);
      wake();
    });
    window.addEventListener('pointerup', () => {
      let touched = false;
      for (const pill of st.pills) { if (pill.on.press) touched = true; pill.on.press = false; }
      if (touched) wake();
    }, { passive: true });
    window.addEventListener('pointercancel', () => {
      for (const pill of st.pills) pill.on.press = false;
      wake();
    }, { passive: true });
    // only keyboard focus keeps it lit — a mouse click shouldn't leave the
    // button glowing after the pointer has moved away.  GW (slice 25 R2):
    // :focus-visible lights with hover parity (upstream on.focus semantics);
    // st.keyboardNav additionally covers roving arrow focus, where the app's
    // keydown handler moves focus programmatically and engines recompute
    // :focus-visible only AFTER this focusin dispatch — hence the re-check on
    // the next task.
    nav.addEventListener('focusin', e => {
      const pill = pillFor(e.target); if (!pill) return;
      const applyFocusLit = () => {
        if (!pill.el.isConnected || document.activeElement !== pill.el) return;
        pill.on.focus = !!(pill.el.matches && (pill.el.matches(':focus-visible') || st.keyboardNav));
        wake();
      };
      applyFocusLit();
      window.setTimeout(applyFocusLit, 0);
    });
    nav.addEventListener('focusout', e => {
      const pill = pillFor(e.target); if (!pill) return;
      pill.on.focus = false; wake();
    });
    // keyboard activation gets the same treatment, rippling from the centre
    nav.addEventListener('keydown', e => {
      if (e.key === 'Tab' || e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') {
        st.keyboardNav = true;
        // GW: the app's roving handler (registered before this listener)
        // re-renders the bar and restores focus synchronously, so the focusin
        // for the new pill fired against the previous pill list and before the
        // engine recomputed :focus-visible.  Re-evaluate once settled.
        window.setTimeout(() => {
          const pill = pillFor(document.activeElement);
          for (const other of st.pills) {
            other.on.focus = other === pill && !!(pill && pill.el.matches && (pill.el.matches(':focus-visible') || st.keyboardNav));
          }
          wake();
        }, 0);
      }
      if (e.key !== 'Enter' && e.key !== ' ' || e.repeat) return;
      const pill = pillFor(e.target); if (!pill) return;
      pill.on.press = true; addRipple(pill, 0, 0); wake();
    });
    nav.addEventListener('keyup', e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const pill = pillFor(e.target); if (!pill) return;
      pill.on.press = false; wake();
    });

    if (window.ResizeObserver) {
      // GW (v3.4): one scheduler for every out-of-band layout change.
      const scheduleMeasure = () => { st.needMeasure = true; if (!st.loopActive) renderStatic(); };
      new ResizeObserver(scheduleMeasure).observe(nav);
      // the header can resize independently of the nav (sticky shell chrome),
      // and the canvas rect is header-relative — track both.
      if (st.header) new ResizeObserver(scheduleMeasure).observe(st.header);
      // the stepbar scrolls horizontally whenever the fixed track (v3.3:
      // 1000px) is wider than the bar. DOM buttons move with the scroll while
      // the canvas stays header-anchored — without re-measuring, the painted
      // ring drifts off its button (reported misalignment, 27 R4).
      nav.addEventListener('scroll', scheduleMeasure, { passive: true });
      // late font swaps shift label metrics after first paint
      // — re-measure once fonts settle.
      if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
        document.fonts.ready.then(scheduleMeasure).catch(() => {});
      }
      window.addEventListener('resize', scheduleMeasure);
    } else {
      window.addEventListener('resize', () => { st.needMeasure = true; if (!st.loopActive) renderStatic(); });
    }

    st.buttons = collectButtons(st);
    st.repaint = renderStatic; // GW: module-level refresh() repaints via this
    st.wake = wake;            // GW: module-level refresh() re-wakes via this
    measure();
    renderStatic();
    // GW (需求25 R3): the selected step breathes at rest — keep the loop alive
    if (st.pills.some(pill => pill.active)) wake();
    return st;
  }

  function refresh() {
    if (dead) return;
    if (!state) {
      init(document.getElementById('episodeStepNav'));
      return;
    }
    state.buttons = collectButtons(state);
    state.needMeasure = true;
    if (!state.loopActive && typeof state.repaint === 'function') {
      state.repaint();
      // GW (需求25 R3): the selected step breathes at rest — keep the loop alive
      if (state.pills.some(pill => pill.active) && typeof state.wake === 'function') state.wake();
    }
  }

  window.EpisodeLiquidMetal = {
    init,
    refresh,
    /* GW: introspection for tests/diagnostics */
    isAnimating() { return !!(state && state.loopActive); },
    isMounted() { return !!(state && state.canvas && state.canvas.isConnected); },
    inspect() {
      const rect = state && state.canvas ? state.canvas.getBoundingClientRect() : null;
      const scaleX = state && rect && rect.width ? state.W / rect.width : 1;
      const scaleY = state && rect && rect.height ? state.H / rect.height : 1;
      return state ? state.pills.map(pill => ({
        step: (pill.el.dataset && pill.el.dataset.step) || '',
        active: pill.active,
        hover: pill.hover,
        hoverTarget: pill.hoverTarget,
        press: pill.press,
        over: pill.on.over,
        focus: pill.on.focus,
        /* GW (v3.6): convert the actual backing-store centre through the
           rendered CSS scale, including clamped overlay boxes and DPR
           rounding, before comparing it with the DOM button. */
        cx: rect ? rect.left + pill.cx / scaleX : 0,
        cy: rect ? rect.top + (state.H - pill.cy) / scaleY : 0
      })) : [];
    }
  };
})(window, document);
