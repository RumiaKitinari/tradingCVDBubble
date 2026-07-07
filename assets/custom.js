window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clientside: {
        refit_y: function(relayoutData) {
            if (!relayoutData) return window.dash_clientside.no_update;
            
            var keys = Object.keys(relayoutData);
            var hasX = keys.some(function(k) { return k.indexOf('xaxis') === 0; });
            if (!hasX) return window.dash_clientside.no_update;

            if (window.__is_refitting) return window.dash_clientside.no_update;

            setTimeout(function() {
                var gd = document.getElementById('main-chart');
                if (!gd || !gd._fullLayout) return;
                
                var YPAD = 0.10;
                var LEFT = {'y':'yaxis', 'y2':'yaxis2', 'y4':'yaxis4'};
                var ax = gd._fullLayout.xaxis;
                if (!ax || !ax.range) return;
                var xr = [ax.range[0], ax.range[1]];
                
                var upd = {};
                Object.keys(LEFT).forEach(function(yref) {
                    var lo = Infinity, hi = -Infinity;
                    gd._fullData.forEach(function(f) {
                        if (f.visible === false || f.visible === 'legendonly') return;
                        if ((f.yaxis || 'y') !== yref) return;
                        var xs = f.x; if (!xs || !xs.length) return;
                        
                        var offset = xs[0];
                        var len = xs.length;
                        var i0 = Math.max(0, Math.floor(xr[0] - offset));
                        var i1 = Math.min(len - 1, Math.ceil(xr[1] - offset));
                        if (i0 > i1 || i0 >= len || i1 < 0) return; 
                        
                        if (f.low && f.high) {
                            var lows = f.low, highs = f.high;
                            for (var i = i0; i <= i1; i++) {
                                if (lows[i]  < lo) lo = lows[i];
                                if (highs[i] > hi) hi = highs[i];
                            }
                        } else if (f.y) {
                            var ys = f.y;
                            for (var i = i0; i <= i1; i++) {
                                var v = ys[i];
                                if (v == null || isNaN(v)) continue;
                                if (v < lo) lo = v;
                                if (v > hi) hi = v;
                            }
                        }
                    });
                    if (lo < hi && lo !== Infinity) {
                        var pad = (hi - lo) * YPAD;
                        var new_lo = lo - pad;
                        var new_hi = hi + pad;
                        
                        var old_range = gd._fullLayout[LEFT[yref]] ? gd._fullLayout[LEFT[yref]].range : null;
                        if (!old_range || Math.abs(old_range[0] - new_lo) > 0.05 || Math.abs(old_range[1] - new_hi) > 0.05) {
                            upd[LEFT[yref] + '.range'] = [new_lo, new_hi];
                        }
                    }
                });
                
                if (Object.keys(upd).length > 0) {
                    window.__is_refitting = true;
                    Plotly.relayout(gd, upd).then(function() {
                        setTimeout(function() { window.__is_refitting = false; }, 200);
                    });
                }
            }, 150);
            
            return window.dash_clientside.no_update;
        }
    }
});
