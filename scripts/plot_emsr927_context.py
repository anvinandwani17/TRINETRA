"""Recreate the spatial comparison figure from evaluator JSON and NPZ outputs."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();folder=Path(args.results);output=Path(args.out)
    if output.exists():raise ValueError('Figure exists; choose a new filename')
    d=json.loads((folder/'emsr927_context_comparison.json').read_text())
    a=np.load(folder/'comparison_arrays.npz',allow_pickle=False)
    t=d['grid']['transform'];height,width=d['grid']['shape']
    extent=[t[2]/1000,(t[2]+width*t[0])/1000,(t[5]+height*t[4])/1000,t[5]/1000]
    x=np.arange(width)*t[0]/1000+t[2]/1000+t[0]/2000
    y=np.arange(height)*t[4]/1000+t[5]/1000+t[4]/2000
    names={'baseline':'Conservative baseline','pilot_ai':'Original pilot AI','pilot_ai_hand_export':'Original pilot AI + HAND'}
    names={k:v for k,v in names.items() if k in a.files}
    fig,axes=plt.subplots(1,len(names),figsize=(4.4*len(names),6),sharex=True,sharey=True,squeeze=False)
    for ax,(key,title) in zip(axes[0],names.items()):
        m=a[key]
        ax.imshow(np.where(m==255,3,m),extent=extent,cmap=ListedColormap(['#edf1f4','#147dfa','#a2a8b1','#ffffff']),vmin=0,vmax=3,interpolation='nearest')
        ax.contour(x,y,a['reference_landslide'].astype(float),levels=[.5],colors=['#eb6b10'],linewidths=.7)
        ax.contour(x,y,a['reference_coverage'].astype(float),levels=[.5],colors=['#8b40b9'],linestyles='--',linewidths=.7)
        ax.set_title(title,fontsize=11);ax.set_xlabel('UTM easting (km)');ax.tick_params(labelsize=8)
    axes[0][0].set_ylabel('UTM northing (km)')
    fig.suptitle('EMSR927 Bidur: spatial context comparison\nLandslide reference and flood candidates are different classes',fontsize=14)
    fig.legend(handles=[Patch(color='#147dfa',label='Flood candidate'),Patch(color='#a2a8b1',label='Unknown inside case'),Patch(color='#edf1f4',label='Below flood threshold'),Patch(facecolor='none',edgecolor='#eb6b10',label='Mapped landslide boundary'),Patch(facecolor='none',edgecolor='#8b40b9',label='Reference analysed coverage')],loc='lower center',ncol=3,fontsize=9)
    fig.subplots_adjust(top=.84,bottom=.18,wspace=.08)
    output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(output,dpi=180,bbox_inches='tight');plt.close(fig)

if __name__=='__main__':main()
