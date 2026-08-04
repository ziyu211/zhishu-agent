import{Z as ce,bW as be,bs as j,l as he,u as Q,bO as Z,e as S,P as J,C as G,Q as T,j as ve,S as W,f as E,g as _,aW as fe,m as U,M as y,n as z,p as H,L as O,aV as pe,t as X,bg as ge,x as me,y as L,W as M,F as xe,w as K,a9 as I,aa as V,aj as x,ai as C,aq as R,ag as A,G as Ce,ah as Re,ad as Se,ab as ke,an as ye}from"./index-DjZhUQ32.js";import{g as Be,N as q}from"./Space-CrgNtogy.js";import{a as ze,N as _e}from"./Checkbox-NKo_aayF.js";function we(e){const{borderColor:o,primaryColor:t,baseColor:r,textColorDisabled:a,inputColorDisabled:v,textColor2:i,opacityDisabled:c,borderRadius:h,fontSizeSmall:d,fontSizeMedium:s,fontSizeLarge:b,heightSmall:f,heightMedium:m,heightLarge:p,lineHeight:k}=e;return Object.assign(Object.assign({},be),{labelLineHeight:k,buttonHeightSmall:f,buttonHeightMedium:m,buttonHeightLarge:p,fontSizeSmall:d,fontSizeMedium:s,fontSizeLarge:b,boxShadow:`inset 0 0 0 1px ${o}`,boxShadowActive:`inset 0 0 0 1px ${t}`,boxShadowFocus:`inset 0 0 0 1px ${t}, 0 0 0 2px ${j(t,{alpha:.2})}`,boxShadowHover:`inset 0 0 0 1px ${t}`,boxShadowDisabled:`inset 0 0 0 1px ${o}`,color:r,colorDisabled:v,colorActive:"#0000",textColor:i,textColorDisabled:a,dotColorActive:t,dotColorDisabled:o,buttonBorderColor:o,buttonBorderColorActive:t,buttonBorderColorHover:o,buttonColor:r,buttonColorActive:r,buttonTextColor:i,buttonTextColorActive:t,buttonTextColorHover:t,opacityDisabled:c,buttonBoxShadowFocus:`inset 0 0 0 1px ${t}, 0 0 0 2px ${j(t,{alpha:.3})}`,buttonBoxShadowHover:"inset 0 0 0 1px #0000",buttonBoxShadow:"inset 0 0 0 1px #0000",buttonBorderRadius:h})}const Fe={common:ce,self:we},$e={name:String,value:{type:[String,Number,Boolean],default:"on"},checked:{type:Boolean,default:void 0},defaultChecked:Boolean,disabled:{type:Boolean,default:void 0},label:String,size:String,onUpdateChecked:[Function,Array],"onUpdate:checked":[Function,Array],checkedValue:{type:Boolean,default:void 0}},Y=ve("n-radio-group");function Ie(e){const o=he(Y,null),{mergedClsPrefixRef:t,mergedComponentPropsRef:r}=Q(e),a=Z(e,{mergedSize(n){var l,u;const{size:g}=e;if(g!==void 0)return g;if(o){const{mergedSizeRef:{value:N}}=o;if(N!==void 0)return N}if(n)return n.mergedSize.value;const D=(u=(l=r==null?void 0:r.value)===null||l===void 0?void 0:l.Radio)===null||u===void 0?void 0:u.size;return D||"medium"},mergedDisabled(n){return!!(e.disabled||o!=null&&o.disabledRef.value||n!=null&&n.disabled.value)}}),{mergedSizeRef:v,mergedDisabledRef:i}=a,c=S(null),h=S(null),d=S(e.defaultChecked),s=W(e,"checked"),b=J(s,d),f=G(()=>o?o.valueRef.value===e.value:b.value),m=G(()=>{const{name:n}=e;if(n!==void 0)return n;if(o)return o.nameRef.value}),p=S(!1);function k(){if(o){const{doUpdateValue:n}=o,{value:l}=e;T(n,l)}else{const{onUpdateChecked:n,"onUpdate:checked":l}=e,{nTriggerFormInput:u,nTriggerFormChange:g}=a;n&&T(n,!0),l&&T(l,!0),u(),g(),d.value=!0}}function w(){i.value||f.value||k()}function F(){w(),c.value&&(c.value.checked=f.value)}function $(){p.value=!1}function B(){p.value=!0}return{mergedClsPrefix:o?o.mergedClsPrefixRef:t,inputRef:c,labelRef:h,mergedName:m,mergedDisabled:i,renderSafeChecked:f,focus:p,mergedSize:v,handleRadioInputChange:F,handleRadioInputBlur:$,handleRadioInputFocus:B}}const P=E({name:"RadioButton",props:$e,setup:Ie,render(){const{mergedClsPrefix:e}=this;return _("label",{class:[`${e}-radio-button`,this.mergedDisabled&&`${e}-radio-button--disabled`,this.renderSafeChecked&&`${e}-radio-button--checked`,this.focus&&[`${e}-radio-button--focus`]]},_("input",{ref:"inputRef",type:"radio",class:`${e}-radio-input`,value:this.value,name:this.mergedName,checked:this.renderSafeChecked,disabled:this.mergedDisabled,onChange:this.handleRadioInputChange,onFocus:this.handleRadioInputFocus,onBlur:this.handleRadioInputBlur}),_("div",{class:`${e}-radio-button__state-border`}),fe(this.$slots.default,o=>!o&&!this.label?null:_("div",{ref:"labelRef",class:`${e}-radio__label`},o||this.label)))}}),Ve=U("radio-group",`
 display: inline-block;
 font-size: var(--n-font-size);
`,[y("splitor",`
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
 `,[U("radio-button",{height:"var(--n-height)",lineHeight:"var(--n-height)"}),y("splitor",{height:"var(--n-height)"})]),U("radio-button",`
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
 `),y("state-border",`
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
 `,[y("state-border",`
 border-top-left-radius: var(--n-button-border-radius);
 border-bottom-left-radius: var(--n-button-border-radius);
 `)]),H("&:last-child",`
 border-top-right-radius: var(--n-button-border-radius);
 border-bottom-right-radius: var(--n-button-border-radius);
 border-right: 1px solid var(--n-button-border-color);
 `,[y("state-border",`
 border-top-right-radius: var(--n-button-border-radius);
 border-bottom-right-radius: var(--n-button-border-radius);
 `)]),O("disabled",`
 cursor: pointer;
 `,[H("&:hover",[y("state-border",`
 transition: box-shadow .3s var(--n-bezier);
 box-shadow: var(--n-button-box-shadow-hover);
 `),O("checked",{color:"var(--n-button-text-color-hover)"})]),z("focus",[H("&:not(:active)",[y("state-border",{boxShadow:"var(--n-button-box-shadow-focus)"})])])]),z("checked",`
 background: var(--n-button-color-active);
 color: var(--n-button-text-color-active);
 border-color: var(--n-button-border-color-active);
 `),z("disabled",`
 cursor: not-allowed;
 opacity: var(--n-opacity-disabled);
 `)])]);function Te(e,o,t){var r;const a=[];let v=!1;for(let i=0;i<e.length;++i){const c=e[i],h=(r=c.type)===null||r===void 0?void 0:r.name;h==="RadioButton"&&(v=!0);const d=c.props;if(h!=="RadioButton"){a.push(c);continue}if(i===0)a.push(c);else{const s=a[a.length-1].props,b=o===s.value,f=s.disabled,m=o===d.value,p=d.disabled,k=(b?2:0)+(f?0:1),w=(m?2:0)+(p?0:1),F={[`${t}-radio-group__splitor--disabled`]:f,[`${t}-radio-group__splitor--checked`]:b},$={[`${t}-radio-group__splitor--disabled`]:p,[`${t}-radio-group__splitor--checked`]:m},B=k<w?$:F;a.push(_("div",{class:[`${t}-radio-group__splitor`,B]}),c)}}return{children:a,isButtonGroup:v}}const De=Object.assign(Object.assign({},X.props),{name:String,value:[String,Number,Boolean],defaultValue:{type:[String,Number,Boolean],default:null},size:String,disabled:{type:Boolean,default:void 0},"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array]}),Ne=E({name:"RadioGroup",props:De,setup(e){const o=S(null),{mergedSizeRef:t,mergedDisabledRef:r,nTriggerFormChange:a,nTriggerFormInput:v,nTriggerFormBlur:i,nTriggerFormFocus:c}=Z(e),{mergedClsPrefixRef:h,inlineThemeDisabled:d,mergedRtlRef:s}=Q(e),b=X("Radio","-radio-group",Ve,Fe,e,h),f=S(e.defaultValue),m=W(e,"value"),p=J(m,f);function k(l){const{onUpdateValue:u,"onUpdate:value":g}=e;u&&T(u,l),g&&T(g,l),f.value=l,a(),v()}function w(l){const{value:u}=o;u&&(u.contains(l.relatedTarget)||c())}function F(l){const{value:u}=o;u&&(u.contains(l.relatedTarget)||i())}xe(Y,{mergedClsPrefixRef:h,nameRef:W(e,"name"),valueRef:p,disabledRef:r,mergedSizeRef:t,doUpdateValue:k});const $=ge("Radio",s,h),B=L(()=>{const{value:l}=t,{common:{cubicBezierEaseInOut:u},self:{buttonBorderColor:g,buttonBorderColorActive:D,buttonBorderRadius:N,buttonBoxShadow:ee,buttonBoxShadowFocus:oe,buttonBoxShadowHover:te,buttonColor:re,buttonColorActive:ae,buttonTextColor:ne,buttonTextColorActive:ie,buttonTextColorHover:se,opacityDisabled:le,[M("buttonHeight",l)]:de,[M("fontSize",l)]:ue}}=b.value;return{"--n-font-size":ue,"--n-bezier":u,"--n-button-border-color":g,"--n-button-border-color-active":D,"--n-button-border-radius":N,"--n-button-box-shadow":ee,"--n-button-box-shadow-focus":oe,"--n-button-box-shadow-hover":te,"--n-button-color":re,"--n-button-color-active":ae,"--n-button-text-color":ne,"--n-button-text-color-hover":se,"--n-button-text-color-active":ie,"--n-height":de,"--n-opacity-disabled":le}}),n=d?me("radio-group",L(()=>t.value[0]),B,e):void 0;return{selfElRef:o,rtlEnabled:$,mergedClsPrefix:h,mergedValue:p,handleFocusout:F,handleFocusin:w,cssVars:d?void 0:B,themeClass:n==null?void 0:n.themeClass,onRender:n==null?void 0:n.onRender}},render(){var e;const{mergedValue:o,mergedClsPrefix:t,handleFocusin:r,handleFocusout:a}=this,{children:v,isButtonGroup:i}=Te(pe(Be(this)),o,t);return(e=this.onRender)===null||e===void 0||e.call(this),_("div",{onFocusin:r,onFocusout:a,ref:"selfElRef",class:[`${t}-radio-group`,this.rtlEnabled&&`${t}-radio-group--rtl`,this.themeClass,i&&`${t}-radio-group--button-group`],style:this.cssVars},v)}}),Ue={class:"share-scope"},He={key:0,class:"role-box"},Ae={key:1,class:"role-hint"},Pe={key:2,class:"role-hint"},We=E({__name:"ShareScopeSelector",props:{shared:{type:Boolean},shareWith:{}},emits:["update:shared","update:shareWith"],setup(e,{emit:o}){const t=e,r=o,a=S("private"),v=[{label:"操作员",value:"operator"},{label:"普通用户",value:"user"},{label:"访客",value:"viewer"}],i=S(!1);function c(){if(i.value){i.value=!1;return}a.value=t.shared?"all":t.shareWith&&t.shareWith.length?"roles":"private"}K(()=>[t.shared,t.shareWith],c,{immediate:!0}),K(a,d=>{d==="all"?(i.value=!0,r("update:shared",!0),r("update:shareWith",[])):d==="private"?(i.value=!0,r("update:shared",!1),r("update:shareWith",[])):(i.value=!0,r("update:shared",!1))});function h(d){r("update:shareWith",d)}return(d,s)=>(I(),V("div",Ue,[x(C(Ne),{value:a.value,"onUpdate:value":s[0]||(s[0]=b=>a.value=b),size:"small"},{default:R(()=>[x(C(q),null,{default:R(()=>[x(C(P),{value:"private"},{default:R(()=>[...s[1]||(s[1]=[A("私有（仅自己）",-1)])]),_:1}),x(C(P),{value:"all"},{default:R(()=>[...s[2]||(s[2]=[A("共享给所有人",-1)])]),_:1}),x(C(P),{value:"roles"},{default:R(()=>[...s[3]||(s[3]=[A("按角色共享",-1)])]),_:1})]),_:1})]),_:1},8,["value"]),a.value==="roles"?(I(),V("div",He,[x(C(ze),{value:e.shareWith,"onUpdate:value":h},{default:R(()=>[x(C(q),null,{default:R(()=>[(I(),V(Ce,null,Re(v,b=>x(C(_e),{key:b.value,value:b.value},{default:R(()=>[A(Se(b.label),1)]),_:2},1032,["value"])),64))]),_:1})]),_:1},8,["value"]),s[4]||(s[4]=ke("div",{class:"role-hint"},"选中的角色成员可见 / 可使用（仅创建者可编辑）",-1))])):a.value==="all"?(I(),V("div",Ae,"所有登录用户可见 / 可使用（仅创建者可编辑）")):(I(),V("div",Pe,"仅你自己可见 / 可管理"))]))}}),Oe=ye(We,[["__scopeId","data-v-b1c2d20d"]]);export{Ne as N,Oe as S,P as a};
