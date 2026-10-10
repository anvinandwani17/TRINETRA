"""Manual inspection PNGs, generated only from actual processing arrays."""
from pathlib import Path
import numpy as np


def previews(folder, before, after, change, observable, reasons, grid):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    transform = grid['transform']
    extent = [transform.c,transform.c+grid['width']*20,
              transform.f-grid['height']*20,transform.f]
    files = []
    for name, data, title in [('before',before,'Before: terrain-flattened Gamma0'),
                              ('after',after,'After: terrain-flattened Gamma0'),
                              ('change',change,'Change: post minus pre (not flood classification)')]:
        fig, axes = plt.subplots(1,2,figsize=(11,5),layout='constrained')
        cmap = plt.get_cmap('RdBu_r' if name == 'change' else 'gray').copy()
        cmap.set_bad('#9c9c9c')
        for index, ax in enumerate(axes):
            im = ax.imshow(np.ma.masked_invalid(data[index]),cmap=cmap,
                           vmin=-10 if name == 'change' else -30,vmax=10 if name == 'change' else 0,
                           extent=extent,origin='upper',interpolation='nearest')
            ax.set_title(['VV','VH'][index]); ax.set_xlabel('UTM easting (m)'); ax.set_ylabel('UTM northing (m)')
            fig.colorbar(im,ax=ax,label='dB')
        fig.suptitle(title+'\nGray = unknown/outside AOI; shared display limits')
        path = folder/(name+'.png'); fig.savefig(path,dpi=140); plt.close(fig); files.append(path)
    display = np.full(observable.shape,3,dtype=np.uint8)
    display[observable == 0] = 0
    display[observable == 1] = 1
    # Orange identifies DEM-estimated shadow or layover; gray is other unknown.
    display[(observable == 0) & ((reasons & (4|8|16|32)) != 0)] = 2
    fig, ax = plt.subplots(figsize=(7,6),layout='constrained')
    ax.imshow(display,cmap=ListedColormap(['#9c9c9c','#38a169','#ed8936','#ffffff']),vmin=0,vmax=3,
              extent=extent,origin='upper',interpolation='nearest')
    ax.set_title('Pair observability (DEM-based estimate)'); ax.set_xlabel('UTM easting (m)'); ax.set_ylabel('UTM northing (m)')
    ax.legend(handles=[Patch(color=c,label=l) for c,l in [('#38a169','Observable in both dates and polarizations'),
                ('#ed8936','Shadow/layover: unobservable'),('#9c9c9c','Other unknown: never dry'),('#ffffff','Outside AOI')]],
                loc='upper center',bbox_to_anchor=(.5,-.13),fontsize=8)
    path = folder/'observability.png'; fig.savefig(path,dpi=140); plt.close(fig); files.append(path)
    return files
