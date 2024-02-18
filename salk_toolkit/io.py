# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_io.ipynb.

# %% auto 0
__all__ = ['max_cats', 'custom_meta_key', 'read_json', 'process_annotated_data', 'read_annotated_data', 'extract_column_meta',
           'group_columns_dict', 'list_aliases', 'change_meta_df', 'change_parquet_meta', 'infer_meta',
           'data_with_inferred_meta', 'read_and_process_data', 'save_population_h5', 'load_population_h5',
           'save_sample_h5', 'save_parquet_with_metadata', 'load_parquet_metadata', 'load_parquet_with_metadata']

# %% ../nbs/01_io.ipynb 3
import json, os, warnings
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import polars as pl
import datetime as dt

from typing import List, Tuple, Dict, Union, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

import salk_toolkit as stk
from salk_toolkit.utils import replace_constants, vod, is_datetime, warn

# %% ../nbs/01_io.ipynb 4
def read_json(fname,replace_const=True):
    with open(fname,'r') as jf:
        meta = json.load(jf)
    if replace_const:
        meta = replace_constants(meta)
    return meta

# %% ../nbs/01_io.ipynb 5
# Default usage with mature metafile: process_annotated_data(<metafile name>)
# When figuring out the metafile, it can also be run as: process_annotated_data(meta=<dict>, data_file=<>)
def process_annotated_data(meta_fname=None, meta=None, data_file=None, return_meta=False, only_fix_categories=False):
    
    # Read metafile
    if meta_fname:
        meta = read_json(meta_fname,replace_const=False)
    
    # Setup constants with a simple replacement mechanic
    constants = meta['constants'] if 'constants' in meta else {}
    meta = replace_constants(meta)
    
    # Read datafile(s)
    opts = meta['read_opts'] if'read_opts' in meta else {}
    if data_file: data_files = [{ 'file': data_file, 'opts': opts}]
    elif 'file' in meta: data_files = [{ 'file': meta['file'], 'opts': opts }]
    elif 'files' in meta:
        data_files = [ {'opts': opts, **f } if isinstance(f,dict) else
                       {'opts': opts, 'file': f } for f in meta['files'] ]
    else: raise Exception("No files provided in metafile")
    
    raw_dfs, meta_inputs = [], False
    for fi, fd in enumerate(data_files):
        
        data_file, opts = fd['file'], fd['opts']
        if meta_fname: data_file = os.path.join(os.path.dirname(meta_fname),data_file)
        
        if data_file[-3:] in ['csv', '.gz']:
            raw_data = pd.read_csv(data_file, low_memory=False, **opts)
        elif data_file[-3:] in ['sav','dta']:
            read_fn = getattr(pyreadstat,'read_'+data_file[-3:])
            with warnings.catch_warnings(): # While pyreadstat has not been updated to pandas 2.2 standards
                warnings.simplefilter("ignore")
                raw_data, _ = read_fn(data_file, **{ 'apply_value_formats':True, 'dates_as_pandas_datetime':True },**opts)
        elif data_file[-7:] == 'parquet':
            raw_data = pd.read_parquet(data_file, **opts)
        elif data_file[-4:] in ['.xls', 'xlsx', 'xlsm', 'xlsb', '.odf', '.ods', '.odt']:
            raw_data = pd.read_excel(data_file, **opts)
        elif data_file[-4:] == 'json': # Allow metafile to load other metafiles as input
            warn(f"Processing {data_file}") # Print this to separate warnings for input jsons from main 
            raw_data, _ = read_annotated_data(data_file)
            meta_inputs = True
        else:
            raise Exception(f"Not a known file format for {data_file}")
        
        # If data is multi-indexed, flatten the index
        if isinstance(raw_data.columns,pd.MultiIndex): raw_data.columns = [" | ".join(tpl) for tpl in raw_data.columns]
        
        # Add extra columns to raw data that contain info about the file. Always includes column 'file' with filename and file_ind with index
        # Can be used to add survey_date or other useful metainfo
        raw_data['file_ind'] = fi
        for k,v in fd.items():
            if k in ['opts']: continue
            raw_data[k] = v
            
        # Re-align the categoricals to the first file, as pandas fails to concatenate if one is ordered and other is not
        if fi>0:
            fdf = raw_dfs[0]
            for c in raw_data.columns:
                if c in fdf.columns and raw_data[c].dtype.name == 'category' and fdf[c].dtype.name == 'category':
                    raw_data[c] = pd.Categorical(raw_data[c],dtype=fdf[c].dtype)
            
        raw_dfs.append(raw_data)
        
    if meta_inputs: warn(f"Processing main meta file") # Print this to separate warnings for input jsons from main 
        
    raw_data = pd.concat(raw_dfs)
    
    globs = {'pd':pd, 'np':np, 'stk':stk, 'df':raw_data, **constants }
    if 'preprocessing' in meta and not only_fix_categories:
        exec(meta['preprocessing'],globs)
        raw_data = globs['df']
    
    ndf = None
    for group in meta['structure']:
        for tpl in group['columns']:
            if type(tpl)==list:
                cn = tpl[0] # column name
                sn = tpl[1] if len(tpl)>1 and type(tpl[1])==str else cn # source column
                cd = tpl[2] if len(tpl)==3 else tpl[1] if len(tpl)==2 and type(tpl[1])==dict else {} # metadata
            else:
                cn = sn = tpl
                cd = {}
                
            if only_fix_categories: sn = cn

            if 'scale' in group: cd = {**group['scale'],**cd}
            
            if sn not in raw_data:
                if not vod(cd,'generated'): # bypass warning for columns marked as being generated later
                    warn(f"Column {sn} not found")
                continue
            
            if raw_data[sn].isna().all():
                warn(f"Column {sn} is empty and thus ignored")
                continue
                
            s = raw_data[sn].rename(cn)
            
            if not only_fix_categories:
                if s.dtype.name=='category': s = s.astype('object') # This makes it easier to use common ops like replace and fillna
                if 'translate' in cd: 
                    s = s.astype('str').replace(cd['translate'])
                if 'transform' in cd: s = eval(cd['transform'],{ 's':s, 'df':raw_data, 'ndf':ndf, 'pd':pd, 'np':np, 'stk':stk , **constants })
                
                if vod(cd,'datetime'): s = pd.to_datetime(s,errors='coerce')
                elif vod(cd,'continuous'): s = pd.to_numeric(s,errors='coerce')

            if 'categories' in cd: 
                na_sum = s.isna().sum()
                
                if cd['categories'] == 'infer':
                    if pd.api.types.is_numeric_dtype(s): cd['categories'] = list(map(lambda v: v.item(),np.sort(s.unique()))) # map to list of native int/float
                    elif s.dtype=='category': cd['categories'] = list(s.dtype.categories) # Categories come from data file
                    elif 'translate' in cd and 'transform' not in cd and set(cd['translate'].values()) >= set(s.unique()): # Infer order from translation dict
                        cd['categories'] = list(pd.unique(np.array(list(cd['translate'].values()))))
                    else: # Just use lexicographic ordering
                        if vod(cd,'ordered',False): warn(f"Ordered category {cn} had category: infer. This only works correctly if you want lexicographic ordering!")
                        cd['categories'] = [ str(c) for c in np.sort(s.unique().astype('str')) if pd.notna(c) ] # Also propagates it into meta (unless shared scale)
                        s = s.astype('str')
                    
                cats = cd['categories']
                
                ns = pd.Series(pd.Categorical(s,categories=cats,ordered=cd['ordered'] if 'ordered' in cd else False), name=cn, index=raw_data.index)
                # Check if the category list provided was comprehensive
                new_nas = ns.isna().sum() - na_sum
                
                if new_nas > 0: 
                    unlisted_cats = set(s.dropna().unique())-set(cats)
                    warn(f"Column {cn} {f'({sn}) ' if cn != sn else ''} had unknown categories {unlisted_cats} for { new_nas/len(ns) :.1%} entries")
                    
                s = ns
            
            # Update ndf in real-time so it would be usable in transforms for next columns
            ndf = pd.concat([ndf,s],axis=1) if ndf is not None else pd.DataFrame(s)

    if 'postprocessing' in meta and not only_fix_categories:
        globs['df'] = ndf
        exec(meta['postprocessing'],globs)
        ndf = globs['df']
    
    return (ndf, meta) if return_meta else ndf

# %% ../nbs/01_io.ipynb 6
# Read either a json annotation and process the data, or a processed parquet with the annotation attached
def read_annotated_data(fname):
    _, ext = os.path.splitext(fname)
    if ext == '.json':
        return process_annotated_data(fname, return_meta=True)
    elif ext == '.parquet':
        data, full_meta = load_parquet_with_metadata(fname)
        if full_meta is not None:
            return data, full_meta['data']
    
    warn(f"Warning: using inferred meta for {fname}")
    meta = infer_meta(fname,meta_file=False)
    return process_annotated_data(fname, meta=meta, return_meta=True)

# %% ../nbs/01_io.ipynb 7
# Helper functions designed to be used with the annotations

# Convert data_meta into a dict where each group and column maps to their metadata dict
def extract_column_meta(data_meta):
    res = defaultdict(lambda: {})
    for g in data_meta['structure']:
        base = g['scale'] if 'scale' in g else {}
        res[g['name']] = base
        for cd in g['columns']:
            if isinstance(cd,str): cd = [cd]
            res[cd[0]] = {**base,**cd[-1]} if isinstance(cd[-1],dict) else base
    return res

# Convert data_meta into a dict of group_name -> [column names]
def group_columns_dict(data_meta):
    return { g['name'] : [(t[0] if type(t)!=str else t) for t in g['columns']] for g in data_meta['structure'] }

# Take a list and a dict and replace all dict keys in list with their corresponding lists in-place
def list_aliases(lst, da):
    return [ fv for v in lst for fv in (da[v] if v in da else [v]) ]

# %% ../nbs/01_io.ipynb 9
# Creates a mapping old -> new
def get_original_column_names(dmeta):
    res = {}
    for g in dmeta['structure']:
        for c in g['columns']:
            if isinstance(c,str): res[c] = c
            if len(c)==1: res[c[0]] = c[0]
            elif len(c)>=2 and isinstance(c[1],str): res[c[1]] = c[0]
    return res

# Map ot backwards and nt forwards to move from one to the other
def change_mapping(ot, nt, only_matches=False):
    # Todo: warn about non-bijective mappings
    matches = { v: nt[k] for k, v in ot.items() if k in nt and v!=nt[k] } # change those that are shared
    if only_matches: return matches
    else: 
        return { **{ v:k for k, v in ot.items() if k not in nt }, # undo those in ot not in nt
                 **{ k:v for k, v in nt.items() if k not in ot }, # do those in nt not in ot
                 **matches } 

# %% ../nbs/01_io.ipynb 10
# Change an existing dataset to correspond better to a new meta_data
# This is intended to allow making small improvements in the meta even after a model has been run
# It is by no means perfect, but is nevertheless a useful tool to avoid re-running long pymc models for simple column/translation changes
def change_meta_df(df, old_dmeta, new_dmeta):
    warn("This tool handles only simple cases of column name, translation and category order changes.")
    
    # Ready the metafiles for parsing
    old_dmeta = replace_constants(old_dmeta); new_dmeta = replace_constants(new_dmeta)
    
    # Rename columns 
    ocn, ncn = get_original_column_names(old_dmeta), get_original_column_names(new_dmeta)
    name_changes = change_mapping(ocn,ncn,only_matches=True)
    if name_changes != {}: print(f"Renaming columns: {name_changes}")
    df.rename(columns=name_changes,inplace=True)
    
    rev_name_changes = { v: k for k,v in name_changes.items() }
    
    # Get metadata for each column
    ocm = extract_column_meta(old_dmeta)
    ncm = extract_column_meta(new_dmeta)
    
    for c in ncm.keys():
        if c not in df.columns: continue # probably group
        if c not in ocm.keys(): continue # new column
        
        ncd, ocd = ncm[c], ocm[rev_name_changes[c] if c in rev_name_changes else c]
        
        # Warn about transformations and don't touch columns where those change
        if vod(ocd,'transform') != vod(ncd,'transform'):
            warn(f"Column {c} has a different transformation. Leaving it unchanged")
            continue
        
        # Handle translation changes
        ot, nt = vod(ocd,'translate',{}), vod(ncd,'translate',{})
        remap = change_mapping(ot,nt)
        if remap != {}: print(f"Remapping {c} with {remap}")
        df[c].replace(remap,inplace=True)
        
        # Reorder categories and/or change ordered status
        if vod(ocd,'categories') != vod(ncd,'categories') or vod(ocd,'ordered') != vod(ncd,'ordered'):
            cats = vod(ncd,'categories')
            if isinstance(cats,list):
                print(f"Changing {c} to Cat({cats},ordered={vod(ncd,'ordered')}")
                df[c] = pd.Categorical(df[c],categories=cats,ordered=vod(ncd,'ordered'))
    
    # column order changes
    gcdict = group_columns_dict(new_dmeta)
    
    cols = ['draw','obs_idx'] + [ c for g in new_dmeta['structure'] for c in gcdict[g['name']]]
    cols = [ c for c in cols if c in df.columns ]
    
    return df[cols]

def change_parquet_meta(orig_file,data_metafile,new_file):
    df, meta = load_parquet_with_metadata(orig_file)
    
    new_data_meta = read_json(data_metafile, replace_const=True)
    df = change_meta_df(df,meta['data'],new_data_meta)
    
    meta['old_data'] = meta['data']
    meta['data'] = new_data_meta
    save_parquet_with_metadata(df,meta,new_file)
    
    return df, meta


# %% ../nbs/01_io.ipynb 11
def is_categorical(col):
    return col.dtype.name in ['object', 'str', 'category'] and not is_datetime(col)


# %% ../nbs/01_io.ipynb 12
max_cats = 50

# Create a very basic metafile for a dataset based on it's contents
# This is not meant to be directly used, rather to speed up the annotation process
def infer_meta(data_file=None, meta_file=True, read_opts={}, df=None, translate_fn=None, translation_blacklist=[], ordinal_ranking=[]):
    meta = { 'constants': {}, 'read_opts': read_opts }
    
    # Read datafile
    col_labels = {}
    if data_file is not None:
        path, fname = os.path.split(data_file)
        meta['file'] = fname
        if data_file[-3:] in ['csv', '.gz']:
            df = pd.read_csv(data_file, low_memory=False, **read_opts)
        elif data_file[-3:] in ['sav','dta']:
            read_fn = getattr(pyreadstat,'read_'+data_file[-3:])
            df, sav_meta = read_fn(data_file, **{ 'apply_value_formats':True, 'dates_as_pandas_datetime':True },**read_opts)
            col_labels = dict(zip(sav_meta.column_names, sav_meta.column_labels)) # Make this data easy to access by putting it in meta as constant
            if translate_fn: col_labels = { k:translate_fn(v) for k,v in col_labels.items() }
        elif data_file[-7:] == 'parquet':
            df = pd.read_parquet(data_file, **read_opts)
        elif data_file[-4:] in ['.xls', 'xlsx', 'xlsm', 'xlsb', '.odf', '.ods', '.odt']:
            df = pd.read_excel(data_file, **read_opts)
        else:
            raise Exception(f"Not a known file format {data_file}")
            
    # If data is multi-indexed, flatten the index
    if isinstance(df.columns,pd.MultiIndex): df.columns = [" | ".join(tpl) for tpl in df.columns]

    cats, grps = {}, defaultdict(lambda: list())
    
    main_grp = { 'name': 'main', 'columns':[] }
    meta['structure'] = [main_grp]
    
    # Remove empty columns
    cols = [ c for c in df.columns if df[c].notna().any() ]
    
    # Determine category lists for all categories
    for cn in cols:
        if not is_categorical(df[cn]): continue
        cats[cn] = sorted(list(df[cn].dropna().unique())) if df[cn].dtype.name != 'category' else list(df[cn].dtype.categories)
        
        for cs in grps:
            #if cn.startswith('Q2_'): print(len(set(cats[cn]) & cs)/len(cs),set(cats[cn]),cs)
            if len(set(cats[cn]) & cs)/len(cs) > 0.75: # match to group if most of the values match
                lst = grps[cs]
                del grps[cs]
                grps[frozenset(cs | set(cats[cn]))] = lst + [cn]
                break
        else:
            grps[frozenset(cats[cn])].append(cn)
        
    # Fn to create the meta for a categorical column
    def cat_meta(cn):
        m = { 'categories': cats[cn] if len(cats[cn])<=max_cats else 'infer' }
        if cn in df.columns and df[cn].dtype=='category' and df[cn].dtype.ordered: m['ordered'] = True
        if translate_fn is not None and cn not in translation_blacklist and len(cats[cn])<=max_cats:
            tdict = { c: translate_fn(c) for c in m['categories'] }
            m['categories'] = 'infer' #[ tdict[c] for c in m['categories'] ]
            m['translate'] = tdict
        return m
        
    
    # Create groups from values that share a category
    handled_cols = set()
    for k,g_cols in grps.items():
        if len(g_cols)<2: continue
        
        # Set up the columns part
        m_cols = []
        for cn in g_cols:
            ce = [cn,{'label': col_labels[cn]}] if cn in col_labels else [cn]
            if translate_fn is not None: ce = [translate_fn(cn)]+ ce
            if len(ce) == 1: ce = ce[0]
            m_cols.append(ce)
        
        cats[str(k)] = list(k) # so cat_meta would use the full list
        grp = { 'name': ';'.join(k), 'scale': cat_meta(str(k)), 'columns': m_cols }
        
        if np.isin(m_cols,ordinal_ranking).any():
            grp['name'], grp['hidden'] = 'ordinal_ranking_raw', True # Set this group to hidden as it is generally weirdly shaped and only used as input to ordinal ranking
            meta['structure'].append({ 'name': 'ordinal_ranking', 'scale': { 'continuous':True, 'generated':True }, 'columns': list(k) })
        
        meta['structure'].append(grp)
        handled_cols.update(g_cols)
        
    # Put the rest of variables into main category
    main_cols = [ c for c in cols if c not in handled_cols ]
    for cn in main_cols:
        if cn in cats: cdesc = cat_meta(cn)
        else: 
            if is_datetime(df[cn]): cdesc = {'datetime':True}
            else: cdesc = {'continuous':True}
        if cn in col_labels: cdesc['label'] = col_labels[cn]
        main_grp['columns'].append([cn,cdesc] if translate_fn is None else [translate_fn(cn),cn,cdesc])
        
    #print(json.dumps(meta,indent=2,ensure_ascii=False))
    
    # Write file to disk
    if data_file is not None and meta_file:
        if meta_file is True: meta_file = os.path.join(path, os.path.splitext(fname)[0]+'_meta.json')
        if not os.path.exists(meta_file):
            print(f"Writing {meta_file} to disk")
            with open(meta_file,'w',encoding='utf8') as jf:
                json.dump(meta,jf,indent=2,ensure_ascii=False)
        else:
            print(f"{meta_file} already exists, skipping write")

    return meta

# Small convenience function to have a meta available for any dataset
def data_with_inferred_meta(data_file, **kwargs):
    meta = infer_meta(data_file,meta_file=False, **kwargs)
    return process_annotated_data(meta=meta, data_file=data_file, return_meta=True)


# %% ../nbs/01_io.ipynb 14
def read_and_process_data(desc, return_meta=False, constants={}):
    df, meta = read_annotated_data(desc['file'])
    
    # Perform transformation and filtering
    globs = {'pd':pd, 'np':np, 'stk':stk, 'df':df, **constants}
    if 'preprocessing' in desc:  exec(desc['preprocessing'], globs)
    if 'filter' in desc: globs['df'] = globs['df'][eval(desc['filter'], globs)]
    if 'postprocessing' in desc: exec(desc['postprocessing'],globs)
    df = globs['df']
    
    return (df, meta) if return_meta else df

# %% ../nbs/01_io.ipynb 16
def save_population_h5(fname,pdf):
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    hdf.put('population',pdf,format='table')
    hdf.close()
    
def load_population_h5(fname):
    hdf =  pd.HDFStore(fname, mode='r')
    res = hdf['population'].copy()
    hdf.close()
    return res

# %% ../nbs/01_io.ipynb 17
def save_sample_h5(fname,trace,COORDS = None, filter_df = None):
    odims = [d for d in trace.predictions.dims if d not in ['chain','draw','obs_idx']]
    
    if COORDS is None: # Recover them from trace (requires posterior be saved in same trace)
        inds = trace.posterior.indexes
        coords = { t: list(inds[t]) for t in inds if t not in ['chain','draw'] and '_dim_' not in t}
        COORDS = { 'immutable': coords, 'mutable': ['obs_idx'] }

    if filter_df is None: # Recover filter dimensions and data from trace (works only for GLMs)
        rmdims = odims + list({'time','unit','combined_inputs'} & set(trace.predictions_constant_data.dims))
        df = trace.predictions_constant_data.drop_dims(rmdims).to_dataframe()#.set_index(demographics_order).indexb
        df.columns = [ s.removesuffix('_id') for s in df.columns]
        df.drop(columns=[c for c in df.columns if c[:4]=='obs_'],inplace=True)

        for d in df.columns:
            if d in COORDS['immutable']:
                fs = COORDS['immutable'][d]
                df[d] = pd.Categorical(df[d].replace(dict(enumerate(fs))),fs)
                if d in orders: df[d] = pd.Categorical(df[d],orders[d],ordered=True)
        filter_df = df

    chains, draws = trace.predictions.dims['chain'], trace.predictions.dims['draw']
    dinds = np.array(list(it.product( range(chains), range(draws), list(filter_df.index)))).reshape( (-1, 3) )

    res_dfs = { 'filter': filter_df }
    for odim in odims:
        response_cols = list(np.array(trace.predictions[odim]))
        xdf = pd.DataFrame(np.concatenate( (
            dinds,
            np.array(trace.predictions['y_'+odim]).reshape( ( -1,len(response_cols) ) )
            ), axis=-1), columns = ['chain', 'draw', 'obs_idx'] + response_cols)
        res_dfs[odim] = postprocess_rdf(xdf,odim)
        
    # Save dfs as hdf5
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    for k,vdf in res_dfs.items():
        hdf.put(k,vdf,format='table')
    hdf.close()


# %% ../nbs/01_io.ipynb 18
# These two very helpful functions are borrowed from https://towardsdatascience.com/saving-metadata-with-dataframes-71f51f558d8e

custom_meta_key = 'salk-toolkit-meta'

def save_parquet_with_metadata(df, meta, file_name):
    table = pa.Table.from_pandas(df)
    
    custom_meta_json = json.dumps(meta)
    existing_meta = table.schema.metadata
    combined_meta = {
        custom_meta_key.encode() : custom_meta_json.encode(),
        **existing_meta
    }
    table = table.replace_schema_metadata(combined_meta)
    
    pq.write_table(table, file_name, compression='GZIP')
    
# Just load the metadata from the parquet file
def load_parquet_metadata(file_name):
    schema = pq.read_schema(file_name)
    if custom_meta_key.encode() in schema.metadata:
        restored_meta_json = schema.metadata[custom_meta_key.encode()]
        restored_meta = json.loads(restored_meta_json)
    else: restored_meta = None
    return restored_meta
    
# Load parquet with metadata
def load_parquet_with_metadata(file_name,lazy=False,**kwargs):
    if lazy: # Load it as a polars lazy dataframe
        meta = load_parquet_metadata(file_name)
        ldf = pl.scan_parquet(file_name,**kwargs)
        return ldf, meta
    
    # Read it as a normal pandas dataframe
    restored_table = pq.read_table(file_name,**kwargs)
    restored_df = restored_table.to_pandas()
    if custom_meta_key.encode() in restored_table.schema.metadata:
        restored_meta_json = restored_table.schema.metadata[custom_meta_key.encode()]
        restored_meta = json.loads(restored_meta_json)
    else: restored_meta = None
    return restored_df, restored_meta


