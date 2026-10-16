"""A line pattern is sampled only inside its image.

Stock MapLibre maps the line onto the image plus one padding texel on each
side, and pads patterns with the *opposite* edge (for tiling), so the outer
sliver of the stroke showed the far edge's colour (faint dots along a
lineburst's light edge) and each repeat was squeezed by two texels. Across
the line it now runs from the first row's centre to the last row's; along
it exactly over the image.
"""

EDITS = [
    ("main",
     "vec2 pos_a=mix(pattern_tl_a*texel_size-texel_size,pattern_br_a*texel_size+texel_size,vec2(x_a,y));"
     "vec2 pos_b=mix(pattern_tl_b*texel_size-texel_size,pattern_br_b*texel_size+texel_size,vec2(x_b,y));",
     "vec2 pos_a=vec2(mix(pattern_tl_a.x,pattern_br_a.x,x_a),mix(pattern_tl_a.y+0.5,pattern_br_a.y-0.5,y))*texel_size;"
     "vec2 pos_b=vec2(mix(pattern_tl_b.x,pattern_br_b.x,x_b),mix(pattern_tl_b.y+0.5,pattern_br_b.y-0.5,y))*texel_size;",
     1),
]
