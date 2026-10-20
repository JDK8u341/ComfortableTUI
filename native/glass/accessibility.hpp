#pragma once
// MSAA simple-child bridge. COM calls are marshalled to the owning STA.
template<class Card> class GlassAccessibility final : public IAccessible {
    std::atomic<ULONG> refs{1};
    std::shared_ptr<Card *> card;
    Card *c()const{return *card;}
    int index(VARIANT v)const{return c()&&v.vt==VT_I4&&v.lVal>0&&v.lVal<=static_cast<LONG>(c()->spec.controls.size())?v.lVal-1:-1;}
    HRESULT string(BSTR *out,const std::string &s){if(!out)return E_POINTER;auto w=utf16(s);*out=SysAllocStringLen(w.data(),static_cast<UINT>(w.size()));return *out?S_OK:E_OUTOFMEMORY;}
  public:
    explicit GlassAccessibility(std::shared_ptr<Card *> owner):card(std::move(owner)){}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void **out)override {
        if(!out)return E_POINTER;*out=nullptr;
        if(id==IID_IUnknown||id==IID_IDispatch||id==IID_IAccessible){*out=static_cast<IAccessible *>(this);AddRef();return S_OK;}return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef()override{return ++refs;}
    ULONG STDMETHODCALLTYPE Release()override{auto n=--refs;if(!n)delete this;return n;}
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT *out)override{if(!out)return E_POINTER;*out=0;return S_OK;}
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT,LCID,ITypeInfo **)override{return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(REFIID,LPOLESTR *,UINT,LCID,DISPID *)override{return DISP_E_UNKNOWNNAME;}
    HRESULT STDMETHODCALLTYPE Invoke(DISPID,REFIID,LCID,WORD,DISPPARAMS *,VARIANT *,EXCEPINFO *,UINT *)override{return DISP_E_MEMBERNOTFOUND;}
    HRESULT STDMETHODCALLTYPE get_accParent(IDispatch **out)override {
        if(!out)return E_POINTER;*out=nullptr;if(!c())return CO_E_OBJNOTCONNECTED;
        return AccessibleObjectFromWindow(c()->hwnd,OBJID_WINDOW,IID_IDispatch,reinterpret_cast<void **>(out));
    }
    HRESULT STDMETHODCALLTYPE get_accChildCount(LONG *out)override{if(!out)return E_POINTER;*out=c()?static_cast<LONG>(c()->spec.controls.size()):0;return S_OK;}
    HRESULT STDMETHODCALLTYPE get_accChild(VARIANT,IDispatch **out)override{if(!out)return E_POINTER;*out=nullptr;return S_FALSE;}
    HRESULT STDMETHODCALLTYPE get_accName(VARIANT v,BSTR *out)override {
        if(!c())return CO_E_OBJNOTCONNECTED;int i=index(v);
        return string(out,i<0?c()->spec.title:c()->spec.controls[i].label.empty()?c()->spec.controls[i].id:c()->spec.controls[i].label);
    }
    HRESULT STDMETHODCALLTYPE get_accValue(VARIANT v,BSTR *out)override {
        if(!c())return CO_E_OBJNOTCONNECTED;int i=index(v);if(i<0)return string(out,"");auto &x=c()->spec.controls[i];
        try{return string(out,glass_control_type(x).accessible_value(x));}catch(...){return E_FAIL;}
    }
    HRESULT STDMETHODCALLTYPE get_accDescription(VARIANT v,BSTR *out)override{return string(out,index(v)<0&&c()?c()->spec.subtitle:"");}
    HRESULT STDMETHODCALLTYPE get_accRole(VARIANT v,VARIANT *out)override {
        if(!out)return E_POINTER;VariantInit(out);out->vt=VT_I4;int i=index(v);out->lVal=ROLE_SYSTEM_PANE;
        if(i>=0)out->lVal=glass_control_type(c()->spec.controls[i]).accessibility_role;return S_OK;
    }
    HRESULT STDMETHODCALLTYPE get_accState(VARIANT v,VARIANT *out)override {
        if(!out)return E_POINTER;VariantInit(out);out->vt=VT_I4;out->lVal=0;int i=index(v);
        if(i>=0){auto &x=c()->spec.controls[i];out->lVal=glass_control_type(x).focusable?STATE_SYSTEM_FOCUSABLE:0;
            if(!x.enabled)out->lVal|=STATE_SYSTEM_UNAVAILABLE;
            if(c()->focused==i&&GetFocus()==c()->hwnd)out->lVal|=STATE_SYSTEM_FOCUSED;
            if(x.kind=="toggle"&&x.value>=.5)out->lVal|=STATE_SYSTEM_CHECKED;}
        if(c()&&!IsWindowVisible(c()->hwnd))out->lVal|=STATE_SYSTEM_INVISIBLE;return S_OK;
    }
    HRESULT STDMETHODCALLTYPE get_accHelp(VARIANT,BSTR *out)override{return string(out,"");}
    HRESULT STDMETHODCALLTYPE get_accHelpTopic(BSTR *out,VARIANT,LONG *id)override{if(out)*out=nullptr;if(id)*id=0;return S_FALSE;}
    HRESULT STDMETHODCALLTYPE get_accKeyboardShortcut(VARIANT,BSTR *out)override{return string(out,"");}
    HRESULT STDMETHODCALLTYPE get_accFocus(VARIANT *out)override{if(!out)return E_POINTER;VariantInit(out);out->vt=VT_I4;out->lVal=c()?c()->focused+1:0;return S_OK;}
    HRESULT STDMETHODCALLTYPE get_accSelection(VARIANT *out)override{if(!out)return E_POINTER;VariantInit(out);return S_FALSE;}
    HRESULT STDMETHODCALLTYPE get_accDefaultAction(VARIANT v,BSTR *out)override{int i=index(v);return string(out,i>=0&&glass_control_type(c()->spec.controls[i]).default_action?glass_control_type(c()->spec.controls[i]).default_action:"");}
    HRESULT STDMETHODCALLTYPE accSelect(LONG flags,VARIANT v)override {
        int i=index(v);if(!c()||i<0)return E_INVALIDARG;
        if(!c()->interactive(i))return E_INVALIDARG;
        if(flags&SELFLAG_TAKEFOCUS){SetForegroundWindow(c()->hwnd);SetFocus(c()->hwnd);c()->focus_control(i);}return S_OK;
    }
    HRESULT STDMETHODCALLTYPE accLocation(LONG *x,LONG *y,LONG *w,LONG *h,VARIANT v)override {
        if(!c())return CO_E_OBJNOTCONNECTED;if(!x||!y||!w||!h)return E_POINTER;
        RECT r{};GetWindowRect(c()->hwnd,&r);int i=index(v);*x=r.left;*y=r.top;*w=r.right-r.left;*h=r.bottom-r.top;
        try{if(i>=0){*x+=static_cast<LONG>(20*c()->host->dpi);*y+=static_cast<LONG>(c()->row_top(i)*c()->host->dpi);*w=static_cast<LONG>((c()->width-40)*c()->host->dpi);*h=static_cast<LONG>(row_height(c()->spec.controls[i])*c()->host->dpi);}}catch(...){return E_FAIL;}return S_OK;
    }
    HRESULT STDMETHODCALLTYPE accNavigate(LONG direction,VARIANT from,VARIANT *out)override {
        if(!out||!c())return E_INVALIDARG;VariantInit(out);int i=index(from)+1,n=static_cast<int>(c()->spec.controls.size());
        if(direction==NAVDIR_FIRSTCHILD)i=1;else if(direction==NAVDIR_LASTCHILD)i=n;else if(direction==NAVDIR_NEXT)i++;else if(direction==NAVDIR_PREVIOUS)i--;else return S_FALSE;
        if(i<1||i>n)return S_FALSE;out->vt=VT_I4;out->lVal=i;return S_OK;
    }
    HRESULT STDMETHODCALLTYPE accHitTest(LONG x,LONG y,VARIANT *out)override {
        if(!c()||!out)return E_INVALIDARG;POINT p{x,y};ScreenToClient(c()->hwnd,&p);int i;
        try{i=c()->hit(p.x/c()->host->dpi,p.y/c()->host->dpi);}catch(...){return E_FAIL;}
        VariantInit(out);out->vt=VT_I4;out->lVal=i+1;return S_OK;
    }
    HRESULT STDMETHODCALLTYPE accDoDefaultAction(VARIANT v)override {
        int i=index(v);if(!c()||i<0||!c()->interactive(i))return E_INVALIDARG;
        try{SetForegroundWindow(c()->hwnd);SetFocus(c()->hwnd);c()->activate(i,28+static_cast<float>(c()->spec.controls[i].value)*(c()->width-56),c()->row_top(i)+16);}catch(...){return E_FAIL;}return S_OK;
    }
    HRESULT STDMETHODCALLTYPE put_accName(VARIANT,BSTR)override{return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE put_accValue(VARIANT v,BSTR value)override {
        int i=index(v);if(!c()||i<0)return E_INVALIDARG;auto &x=c()->spec.controls[i];
        if(x.kind!="input"||!x.enabled||SysStringLen(value)>2048)return E_INVALIDARG;
        x.text=utf8(value?std::wstring(value,SysStringLen(value)):std::wstring{});c()->dirty=true;c()->change(x);return S_OK;
    }
};
