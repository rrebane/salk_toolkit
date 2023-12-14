import streamlit as st
from streamlit_dimensions import st_dimensions
import warnings

def get_plot_width(key):
    wobj = st_dimensions(key=key) or { 'width': 400 }# Can return none so handle that
    return int(0.85*wobj['width']) # Needs to be adjusted to leave margin

warnings.simplefilter(action='ignore', category=FutureWarning)

profile = False
if profile:
    from streamlit_profiler import Profiler
    p = Profiler(); p.start()

st.set_page_config(
    layout="wide",
    page_title="SALK Magic Model",
    #page_icon="./s-model.png",
    initial_sidebar_state="expanded",
)

info = st.empty()

with st.spinner("Loading libraries.."):
    import pandas as pd
    import numpy as np
    import json, io, gzip, os
    from collections import defaultdict

    import arviz as az
    import altair as alt
    import itertools as it
    import warnings
    import contextlib

    from pandas.api.types import is_numeric_dtype

    from salk_toolkit.io import load_parquet_with_metadata, read_json, extract_column_meta
    from salk_toolkit.pp import *
    from salk_toolkit.plots import matching_plots, get_plot_meta
    from salk_toolkit.utils import vod

    tqdm = lambda x: x # So we can freely copy-paste from notebooks

# Turn off annoying warnings
warnings.filterwarnings(action='ignore', category=UserWarning)
warnings.filterwarnings(action='ignore', category=pd.errors.PerformanceWarning)

# If none, uses the meta of the first data source
global_data_metafile = None #'./data/master_meta.json'

if global_data_metafile:
    global_data_meta = read_json(global_data_metafile,replace_const=True)
else: global_data_meta = None

#info.info("Libraries loaded.")
#path = '../salk_internal_package/samples/'
path = './samples/'

input_file_choices = sorted([ f for f in os.listdir(path) if f[-8:]=='.parquet' ])

input_files = st.sidebar.multiselect('Select files:',input_file_choices)

########################################################################
#                                                                      #
#                       LOAD PYMC SAMPLE DATA                          #
#                                                                      #
########################################################################

@st.cache_resource(show_spinner=False)
def load_file(input_file):
    full_df, meta = load_parquet_with_metadata(path+input_file)
    if meta is None: meta = {}
    return { 'data': full_df, 'data_meta': vod(meta,'data',None), 'model_meta': vod(meta,'model') }

if len(input_files)==0:
    st.markdown("""Please choose an input file from the sidebar""")
    st.stop()
else:
    loaded = { ifile:load_file(ifile) for ifile in input_files }
    first_file = loaded[input_files[0]]
    first_data_meta = first_file['data_meta'] if global_data_meta is None else global_data_meta
    first_data = first_file['data']

########################################################################
#                                                                      #
#                            Sidebar UI                                #
#                                                                      #
########################################################################

from pandas.api.types import is_datetime64_any_dtype as is_datetime

def get_dimensions(data_meta, observations=True, whitelist=None):
    res = []
    for g in data_meta['structure']:
        if vod(g,'hidden'): continue
        if 'scale' in g and observations:
            res.append(g['name'])
        else:
            cols = [ (c if isinstance(c,str) else c[0]) for c in g['columns']]
            if whitelist is not None: cols = [ c for c in cols if c in whitelist ]
            res += cols
    return res

args = {}

c_meta = extract_column_meta(first_data_meta)

with st.sidebar: #.expander("Select dimensions"):
    f_info = st.empty()
    st.markdown("""___""")

    if 'training_subsample' in first_data.columns:
        poststrat = st.checkbox('Post-stratified?', True)
    else: poststrat = True


    show_grouped = st.checkbox('Show grouped facets', True)

    obs_dims = get_dimensions(first_data_meta, show_grouped, first_data.columns)
    obs_dims = [c for c in obs_dims if c not in first_data.columns or not is_datetime(first_data[c])]
    all_dims = get_dimensions(first_data_meta, False, first_data.columns)

    obs_name = st.selectbox('Observation', obs_dims)
    args['res_col'] = obs_name

    all_dims = vod(c_meta[obs_name],'modifiers', []) + all_dims

    facet_dims = all_dims
    if len(input_files)>1: facet_dims = ['input_file'] + facet_dims

    facet_dim = st.sidebar.selectbox('Facet:', ['None'] + facet_dims)

    args['factor_cols'] = [facet_dim] if facet_dim != 'None' else []

    if facet_dim != 'None':
        second_dim = st.sidebar.selectbox('Facet 2:', ['None'] + all_dims)
        if second_dim != 'None':  args['factor_cols'] = [facet_dim, second_dim]

    args['internal_facet'] = st.checkbox('Internal facet?',True)

    args['plot'] = st.selectbox('Plot type',matching_plots(args, first_data, first_data_meta))

    plot_args = {}
    for k, v in vod(get_plot_meta(args['plot']),'args',{}).items():
        if v=='bool':
            plot_args[k] = st.checkbox(k)
        elif isinstance(v, list):
            plot_args[k] = st.selectbox(k,v)

    args['plot_args'] = plot_args


    with st.sidebar.expander('Filters'):
        filter_vals = { col: list(first_data[col].unique()) for col in all_dims if col in first_data.columns }
        filters = {}

        for cn in all_dims:
            col = first_data[cn]
            if col.dtype.name=='category' and not col.dtype.ordered:
                filters[cn] = st.selectbox(cn, ['All'] + list(col.dtype.categories))
            elif col.dtype.name=='category':
                cats = col.dtype.categories
                filters[cn] = st.select_slider(cn,cats,value=(cats[0],cats[-1]))
            #elif col.dtype!='bool':
            #    mima = (col.min(),col.max())
            #    filters[cn] = st.slider(cn,*mima,value=mima)

        args['filter'] = { k:v for k,v in filters.items() if v != 'All'}

        if not poststrat:  args['filter']['training_subsample'] = True

        #print(args['filter'])


    x='''
    gcols = group_columns_dict(data_meta)
    for g in data_meta['structure']:
        with st.sidebar.expander(g['name']):
            for cn in gcols[g['name']]:
                if cn not in all_dims: continue
                col = first_data[cn]
                if col.dtype.name=='category' and not col.dtype.ordered:
                    filters[cn] = st.selectbox(cn, ['All'] + list(col.dtype.categories))
                elif col.dtype.name=='category':
                    cats = col.dtype.categories
                    filters[cn] = st.select_slider(cn,cats,value=(cats[0],cats[-1]))
                elif col.dtype!='bool':
                    mima = (col.min(),col.max())
                    filters[cn] = st.slider(cn,*mima,value=mima)
    '''


#left, middle, right = st.columns([2, 5, 2])
#tab = middle.radio('Tabs',['Main'],horizontal=True,label_visibility='hidden')
st.markdown("""___""")

########################################################################
#                                                                      #
#                              GRAPHS                                  #
#                                                                      #
########################################################################




# Create columns, one per input file
if len(input_files)>1 and facet_dim != 'input_file':
    cols = st.columns(len(input_files))
else: cols = [contextlib.suppress()]

if facet_dim == 'input_file':
    with st.spinner('Filtering data...'):
        dfs = []
        for ifile in input_files:
            fargs = args.copy()
            fargs['filter'] = { k:v for k,v in args['filter'].items() if k in loaded[ifile]['data'].columns }
            df = get_filtered_data(loaded[ifile]['data'], first_data_meta, **fargs)
            df['input_file'] = ifile
            dfs.append(df)
        fdf = pd.concat(dfs)
        fdf['input_file'] = pd.Categorical(fdf['input_file'],input_files)
        plot = create_plot(fdf,first_data_meta,width=get_plot_width('full'),**args)

    st.altair_chart(plot,use_container_width=True)

else:
    # Iterate over input files
    for i, ifile in enumerate(input_files):
        with cols[i]:
            # Heading:
            st.header(os.path.splitext(ifile.replace('_',' '))[0])

            data_meta = loaded[ifile]['data_meta'] if global_data_meta is None else global_data_meta
            if data_meta is None: data_meta = first_data_meta

            with st.spinner('Filtering data...'):
                fargs = args.copy()
                fargs['filter'] = { k:v for k,v in args['filter'].items() if k in loaded[ifile]['data'].columns }
                fdf = get_filtered_data(loaded[ifile]['data'], data_meta, **fargs)

                plot = create_plot(fdf,data_meta,width=get_plot_width(f'{i}_{ifile}'),**fargs)

            n_questions = fdf['question'].nunique() if 'question' in fdf else 1
            st.write('Based on %.1f%% of data' % (100*len(fdf)/(len(loaded[ifile]['data'])*n_questions)))
            st.altair_chart(plot,
                use_container_width=(len(input_files)>1)
                )

            with st.expander('Data Meta'):
                st.json(loaded[ifile]['data_meta'])

            with st.expander('Model Meta'):
                st.json(loaded[ifile]['model_meta'])


st.markdown("""***""")
st.caption('Andmed & teostus: **SALK 2023**')
info.empty()

if profile:
    p.stop()
