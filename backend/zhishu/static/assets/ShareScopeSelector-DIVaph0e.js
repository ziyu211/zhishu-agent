import{Z as ce,bV as be,br as G,l as he,u as Z,bN as q,e as y,P as J,C as j,Q as T,j as ve,S as E,f as W,g as _,aV as fe,m as U,M as k,n as z,p as H,L,aU as pe,t as X,bf as ge,x as me,y as M,W as O,F as xe,w as K,a8 as I,a9 as V,ai as x,ah as C,ap as R,af as A,G as Ce,ag as Re,ac as Se,aa as ke,am as ye}from"./index-BWhOU2Qy.js";import{g as Be,N as Q}from"./Space-B-84blHg.js";import{a as ze,N as _e}from"./Checkbox-CwD7yRv3.js";function we(e){const{borderColor:o,primaryColor:t,baseColor:r,textColorDisabled:a,inputColorDisabled:h,textColor2:d,opacityDisabled:c,borderRadius:l,fontSizeSmall:n,fontSizeMedium:b,fontSizeLarge:g,heightSmall:v,heightMedium:m,heightLarge:f,lineHeight:S}=e;return Object.assign(Object.assign({},be),{labelLineHeight:S,buttonHeightSmall:v,buttonHeightMedium:m,buttonHeightLarge:f,fontSizeSmall:n,fontSizeMedium:b,fontSizeLarge:g,boxShadow:`inset 0 0 0 1px ${o}`,boxShadowActive:`inset 0 0 0 1px ${t}`,boxShadowFocus:`inset 0 0 0 1px ${t}, 0 0 0 2px ${G(t,{alpha:.2})}`,boxShadowHover:`inset 0 0 0 1px ${t}`,boxShadowDisabled:`inset 0 0 0 1px ${o}`,color:r,colorDisabled:h,colorActive:"#0000",textColor:d,textColorDisabled:a,dotColorActive:t,dotColorDisabled:o,buttonBorderColor:o,buttonBorderColorActive:t,buttonBorderColorHover:o,buttonColor:r,buttonColorActive:r,buttonTextColor:d,buttonTextColorActive:t,buttonTextColorHover:t,opacityDisabled:c,buttonBoxShadowFocus:`inset 0 0 0 1px ${t}, 0 0 0 2px ${G(t,{alpha:.3})}`,buttonBoxShadowHover:"inset 0 0 0 1px #0000",buttonBoxShadow:"inset 0 0 0 1px #0000",buttonBorderRadius:l})}const Fe={common:ce,self:we},$e={name:String,value:{type:[String,Number,Boolean],default:"on"},checked:{type:Boolean,default:void 0},defaultChecked:Boolean,disabled:{type:Boolean,default:void 0},label:String,size:String,onUpdateChecked:[Function,Array],"onUpdate:checked":[Function,Array],checkedValue:{type:Boolean,default:void 0}},Y=ve("n-radio-group");function Ie(e){const o=he(Y,null),{mergedClsPrefixRef:t,mergedComponentPropsRef:r}=Z(e),a=q(e,{mergedSize(i){var s,u;const{size:p}=e;if(p!==void 0)return p;if(o){const{mergedSizeRef:{value:D}}=o;if(D!==void 0)return D}if(i)return i.mergedSize.value;const N=(u=(s=r==null?void 0:r.value)===null||s===void 0?void 0:s.Radio)===null||u===void 0?void 0:u.size;return N||"medium"},mergedDisabled(i){return!!(e.disabled||o!=null&&o.disabledRef.value||i!=null&&i.disabled.value)}}),{mergedSizeRef:h,mergedDisabledRef:d}=a,c=y(null),l=y(null),n=y(e.defaultChecked),b=E(e,"checked"),g=J(b,n),v=j(()=>o?o.valueRef.value===e.value:g.value),m=j(()=>{const{name:i}=e;if(i!==void 0)return i;if(o)return o.nameRef.value}),f=y(!1);function S(){if(o){const{doUpdateValue:i}=o,{value:s}=e;T(i,s)}else{const{onUpdateChecked:i,"onUpdate:checked":s}=e,{nTriggerFormInput:u,nTriggerFormChange:p}=a;i&&T(i,!0),s&&T(s,!0),u(),p(),n.value=!0}}function w(){d.value||v.value||S()}function F(){w(),c.value&&(c.value.checked=v.value)}function $(){f.value=!1}function B(){f.value=!0}return{mergedClsPrefix:o?o.mergedClsPrefixRef:t,inputRef:c,labelRef:l,mergedName:m,mergedDisabled:d,renderSafeChecked:v,focus:f,mergedSize:h,handleRadioInputChange:F,handleRadioInputBlur:$,handleRadioInputFocus:B}}const P=W({name:"RadioButton",props:$e,setup:Ie,render(){const{mergedClsPrefix:e}=this;return _("label",{class:[`${e}-radio-button`,this.mergedDisabled&&`${e}-radio-button--disabled`,this.renderSafeChecked&&`${e}-radio-button--checked`,this.focus&&[`${e}-radio-button--focus`]]},_("input",{ref:"inputRef",type:"radio",class:`${e}-radio-input`,value:this.value,name:this.mergedName,checked:this.renderSafeChecked,disabled:this.mergedDisabled,onChange:this.handleRadioInputChange,onFocus:this.handleRadioInputFocus,onBlur:this.handleRadioInputBlur}),_("div",{class:`${e}-radio-button__state-border`}),fe(this.$slots.default,o=>!o&&!this.label?null:_("div",{ref:"labelRef",class:`${e}-radio__label`},o||this.label)))}}),Ve=U("radio-group",`
 display: inline-block;
 font-size: var(--n-font-size);
`,[k("splitor",`
 display: inline-block;
 vertical-align: bottom;
 width: 1px;
 transition:
 background-color .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 background: var(--n-button-border-color);
 `,[z("checked",{backgroundColor:"var(--n-button-border-color-active)"}),z("disabled",{opacity:"var(--n-opacity-disabled)"})]),z("button-group",`
 white-space: nowrap;
 height: var(--n-height);
 line-height: var(--n-height);
 `,[U("radio-button",{height:"var(--n-height)",lineHeight:"var(--n-height)"}),k("splitor",{height:"var(--n-height)"})]),U("radio-button",`
 vertical-align: bottom;
 outline: none;
 position: relative;
 user-select: none;
 -webkit-user-select: none;
 display: inline-block;
 box-sizing: border-box;
 padding-left: 14px;
 padding-right: 14px;
 white-space: nowrap;
 transition:
 background-color .3s var(--n-bezier),
 opacity .3s var(--n-bezier),
 border-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 background: var(--n-button-color);
 color: var(--n-button-text-color);
 border-top: 1px solid var(--n-button-border-color);
 border-bottom: 1px solid var(--n-button-border-color);
 `,[U("radio-input",`
 pointer-events: none;
 position: absolute;
 border: 0;
 border-radius: inherit;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 opacity: 0;
 z-index: 1;
 `),k("state-border",`
 z-index: 1;
 pointer-events: none;
 position: absolute;
 box-shadow: var(--n-button-box-shadow);
 transition: box-shadow .3s var(--n-bezier);
 left: -1px;
 bottom: -1px;
 right: -1px;
 top: -1px;
 `),H("&:first-child",`
 border-top-left-radius: var(--n-button-border-radius);
 border-bottom-left-radius: var(--n-button-border-radius);
 border-left: 1px solid var(--n-button-border-color);
 `,[k("state-border",`
 border-top-left-radius: var(--n-button-border-radius);
 border-bottom-left-radius: var(--n-button-border-radius);
 `)]),H("&:last-child",`
 border-top-right-radius: var(--n-button-border-radius);
 border-bottom-right-radius: var(--n-button-border-radius);
 border-right: 1px solid var(--n-button-border-color);
 `,[k("state-border",`
 border-top-right-radius: var(--n-button-border-radius);
 border-bottom-right-radius: var(--n-button-border-radius);
 `)]),L("disabled",`
 cursor: pointer;
 `,[H("&:hover",[k("state-border",`
 transition: box-shadow .3s var(--n-bezier);
 box-shadow: var(--n-button-box-shadow-hover);
 `),L("checked",{color:"var(--n-button-text-color-hover)"})]),z("focus",[H("&:not(:active)",[k("state-border",{boxShadow:"var(--n-button-box-shadow-focus)"})])])]),z("checked",`
 background: var(--n-button-color-active);
 color: var(--n-button-text-color-active);
 border-color: var(--n-button-border-color-active);
 `),z("disabled",`
 cursor: not-allowed;
 opacity: var(--n-opacity-disabled);
 `)])]);function Te(e,o,t){var r;const a=[];let h=!1;for(let d=0;d<e.length;++d){const c=e[d],l=(r=c.type)===null||r===void 0?void 0:r.name;l==="RadioButton"&&(h=!0);const n=c.props;if(l!=="RadioButton"){a.push(c);continue}if(d===0)a.push(c);else{const b=a[a.length-1].props,g=o===b.value,v=b.disabled,m=o===n.value,f=n.disabled,S=(g?2:0)+(v?0:1),w=(m?2:0)+(f?0:1),F={[`${t}-radio-group__splitor--disabled`]:v,[`${t}-radio-group__splitor--checked`]:g},$={[`${t}-radio-group__splitor--disabled`]:f,[`${t}-radio-group__splitor--checked`]:m},B=S<w?$:F;a.push(_("div",{class:[`${t}-radio-group__splitor`,B]}),c)}}return{children:a,isButtonGroup:h}}const Ne=Object.assign(Object.assign({},X.props),{name:String,value:[String,Number,Boolean],defaultValue:{type:[String,Number,Boolean],default:null},size:String,disabled:{type:Boolean,default:void 0},"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array]}),De=W({name:"RadioGroup",props:Ne,setup(e){const o=y(null),{mergedSizeRef:t,mergedDisabledRef:r,nTriggerFormChange:a,nTriggerFormInput:h,nTriggerFormBlur:d,nTriggerFormFocus:c}=q(e),{mergedClsPrefixRef:l,inlineThemeDisabled:n,mergedRtlRef:b}=Z(e),g=X("Radio","-radio-group",Ve,Fe,e,l),v=y(e.defaultValue),m=E(e,"value"),f=J(m,v);function S(s){const{onUpdateValue:u,"onUpdate:value":p}=e;u&&T(u,s),p&&T(p,s),v.value=s,a(),h()}function w(s){const{value:u}=o;u&&(u.contains(s.relatedTarget)||c())}function F(s){const{value:u}=o;u&&(u.contains(s.relatedTarget)||d())}xe(Y,{mergedClsPrefixRef:l,nameRef:E(e,"name"),valueRef:f,disabledRef:r,mergedSizeRef:t,doUpdateValue:S});const $=ge("Radio",b,l),B=M(()=>{const{value:s}=t,{common:{cubicBezierEaseInOut:u},self:{buttonBorderColor:p,buttonBorderColorActive:N,buttonBorderRadius:D,buttonBoxShadow:ee,buttonBoxShadowFocus:oe,buttonBoxShadowHover:te,buttonColor:re,buttonColorActive:ae,buttonTextColor:ne,buttonTextColorActive:ie,buttonTextColorHover:se,opacityDisabled:le,[O("buttonHeight",s)]:de,[O("fontSize",s)]:ue}}=g.value;return{"--n-font-size":ue,"--n-bezier":u,"--n-button-border-color":p,"--n-button-border-color-active":N,"--n-button-border-radius":D,"--n-button-box-shadow":ee,"--n-button-box-shadow-focus":oe,"--n-button-box-shadow-hover":te,"--n-button-color":re,"--n-button-color-active":ae,"--n-button-text-color":ne,"--n-button-text-color-hover":se,"--n-button-text-color-active":ie,"--n-height":de,"--n-opacity-disabled":le}}),i=n?me("radio-group",M(()=>t.value[0]),B,e):void 0;return{selfElRef:o,rtlEnabled:$,mergedClsPrefix:l,mergedValue:f,handleFocusout:F,handleFocusin:w,cssVars:n?void 0:B,themeClass:i==null?void 0:i.themeClass,onRender:i==null?void 0:i.onRender}},render(){var e;const{mergedValue:o,mergedClsPrefix:t,handleFocusin:r,handleFocusout:a}=this,{children:h,isButtonGroup:d}=Te(pe(Be(this)),o,t);return(e=this.onRender)===null||e===void 0||e.call(this),_("div",{onFocusin:r,onFocusout:a,ref:"selfElRef",class:[`${t}-radio-group`,this.rtlEnabled&&`${t}-radio-group--rtl`,this.themeClass,d&&`${t}-radio-group--button-group`],style:this.cssVars},h)}}),Ue={class:"share-scope"},He={key:0,class:"role-box"},Ae={key:1,class:"role-hint"},Pe={key:2,class:"role-hint"},Ee=W({__name:"ShareScopeSelector",props:{shared:{type:Boolean},shareWith:{}},emits:["update:shared","update:shareWith"],setup(e,{emit:o}){const t=e,r=o,a=y("private"),h=[{label:"操作员",value:"operator"},{label:"普通用户",value:"user"},{label:"访客",value:"viewer"}];function d(){a.value=t.shared?"all":t.shareWith&&t.shareWith.length?"roles":"private"}K(()=>[t.shared,t.shareWith],d,{immediate:!0}),K(a,l=>{l==="all"?(r("update:shared",!0),r("update:shareWith",[])):l==="private"?(r("update:shared",!1),r("update:shareWith",[])):r("update:shared",!1)});function c(l){r("update:shareWith",l)}return(l,n)=>(I(),V("div",Ue,[x(C(De),{value:a.value,"onUpdate:value":n[0]||(n[0]=b=>a.value=b),size:"small"},{default:R(()=>[x(C(Q),null,{default:R(()=>[x(C(P),{value:"private"},{default:R(()=>[...n[1]||(n[1]=[A("私有（仅自己）",-1)])]),_:1}),x(C(P),{value:"all"},{default:R(()=>[...n[2]||(n[2]=[A("共享给所有人",-1)])]),_:1}),x(C(P),{value:"roles"},{default:R(()=>[...n[3]||(n[3]=[A("按角色共享",-1)])]),_:1})]),_:1})]),_:1},8,["value"]),a.value==="roles"?(I(),V("div",He,[x(C(ze),{value:e.shareWith,"onUpdate:value":c},{default:R(()=>[x(C(Q),null,{default:R(()=>[(I(),V(Ce,null,Re(h,b=>x(C(_e),{key:b.value,value:b.value},{default:R(()=>[A(Se(b.label),1)]),_:2},1032,["value"])),64))]),_:1})]),_:1},8,["value"]),n[4]||(n[4]=ke("div",{class:"role-hint"},"选中的角色成员可见 / 可使用（仅创建者可编辑）",-1))])):a.value==="all"?(I(),V("div",Ae,"所有登录用户可见 / 可使用（仅创建者可编辑）")):(I(),V("div",Pe,"仅你自己可见 / 可管理"))]))}}),Le=ye(Ee,[["__scopeId","data-v-a10814f4"]]);export{De as N,Le as S,P as a};
