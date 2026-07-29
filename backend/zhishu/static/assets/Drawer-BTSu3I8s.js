import{f as Y,an as N,g as u,c2 as fe,T as _,s as be,bE as me,b2 as U,e as B,u as K,bc as ge,aX as ve,w as we,c as pe,c3 as ye,y as w,l as ze,bF as V,c4 as $e,F as T,J as xe,E as Se,I as Be,p as i,c5 as H,m as c,n as g,M as $,bp as Ce,bI as Ee,bH as ke,bK as Re,t as q,P as L,c6 as Fe,x as Te,c7 as He,Q as x,c8 as Me,S as j,z as X}from"./index-CisyZC-D.js";const Ie=Y({name:"NDrawerContent",inheritAttrs:!1,props:{blockScroll:Boolean,show:{type:Boolean,default:void 0},displayDirective:{type:String,required:!0},placement:{type:String,required:!0},contentClass:String,contentStyle:[Object,String],nativeScrollbar:{type:Boolean,required:!0},scrollbarProps:Object,trapFocus:{type:Boolean,default:!0},autoFocus:{type:Boolean,default:!0},showMask:{type:[Boolean,String],required:!0},maxWidth:Number,maxHeight:Number,minWidth:Number,minHeight:Number,resizable:Boolean,onClickoutside:Function,onAfterLeave:Function,onAfterEnter:Function,onEsc:Function},setup(e){const o=B(!!e.show),t=B(null),p=ze(V);let b=0,S="",f=null;const y=B(!1),v=B(!1),C=w(()=>e.placement==="top"||e.placement==="bottom"),{mergedClsPrefixRef:M,mergedRtlRef:I}=K(e),O=ge("Drawer",I,M),k=r,D=n=>{v.value=!0,b=C.value?n.clientY:n.clientX,S=document.body.style.cursor,document.body.style.cursor=C.value?"ns-resize":"ew-resize",document.body.addEventListener("mousemove",m),document.body.addEventListener("mouseleave",k),document.body.addEventListener("mouseup",r)},R=()=>{f!==null&&(window.clearTimeout(f),f=null),v.value?y.value=!0:f=window.setTimeout(()=>{y.value=!0},300)},W=()=>{f!==null&&(window.clearTimeout(f),f=null),y.value=!1},{doUpdateHeight:A,doUpdateWidth:P}=p,E=n=>{const{maxWidth:a}=e;if(a&&n>a)return a;const{minWidth:d}=e;return d&&n<d?d:n},F=n=>{const{maxHeight:a}=e;if(a&&n>a)return a;const{minHeight:d}=e;return d&&n<d?d:n};function m(n){var a,d;if(v.value)if(C.value){let h=((a=t.value)===null||a===void 0?void 0:a.offsetHeight)||0;const z=b-n.clientY;h+=e.placement==="bottom"?z:-z,h=F(h),A(h),b=n.clientY}else{let h=((d=t.value)===null||d===void 0?void 0:d.offsetWidth)||0;const z=b-n.clientX;h+=e.placement==="right"?z:-z,h=E(h),P(h),b=n.clientX}}function r(){v.value&&(b=0,v.value=!1,document.body.style.cursor=S,document.body.removeEventListener("mousemove",m),document.body.removeEventListener("mouseup",r),document.body.removeEventListener("mouseleave",k))}ve(()=>{e.show&&(o.value=!0)}),we(()=>e.show,n=>{n||r()}),pe(()=>{r()});const s=w(()=>{const{show:n}=e,a=[[U,n]];return e.showMask||a.push([$e,e.onClickoutside,void 0,{capture:!0}]),a});function l(){var n;o.value=!1,(n=e.onAfterLeave)===null||n===void 0||n.call(e)}return ye(w(()=>e.blockScroll&&o.value)),T(xe,t),T(Se,null),T(Be,null),{bodyRef:t,rtlEnabled:O,mergedClsPrefix:p.mergedClsPrefixRef,isMounted:p.isMountedRef,mergedTheme:p.mergedThemeRef,displayed:o,transitionName:w(()=>({right:"slide-in-from-right-transition",left:"slide-in-from-left-transition",top:"slide-in-from-top-transition",bottom:"slide-in-from-bottom-transition"})[e.placement]),handleAfterLeave:l,bodyDirectives:s,handleMousedownResizeTrigger:D,handleMouseenterResizeTrigger:R,handleMouseleaveResizeTrigger:W,isDragging:v,isHoverOnResizeTrigger:y}},render(){const{$slots:e,mergedClsPrefix:o}=this;return this.displayDirective==="show"||this.displayed||this.show?N(u("div",{role:"none"},u(fe,{disabled:!this.showMask||!this.trapFocus,active:this.show,autoFocus:this.autoFocus,onEsc:this.onEsc},{default:()=>u(_,{name:this.transitionName,appear:this.isMounted,onAfterEnter:this.onAfterEnter,onAfterLeave:this.handleAfterLeave},{default:()=>N(u("div",be(this.$attrs,{role:"dialog",ref:"bodyRef","aria-modal":"true",class:[`${o}-drawer`,this.rtlEnabled&&`${o}-drawer--rtl`,`${o}-drawer--${this.placement}-placement`,this.isDragging&&`${o}-drawer--unselectable`,this.nativeScrollbar&&`${o}-drawer--native-scrollbar`]}),[this.resizable?u("div",{class:[`${o}-drawer__resize-trigger`,(this.isDragging||this.isHoverOnResizeTrigger)&&`${o}-drawer__resize-trigger--hover`],onMouseenter:this.handleMouseenterResizeTrigger,onMouseleave:this.handleMouseleaveResizeTrigger,onMousedown:this.handleMousedownResizeTrigger}):null,this.nativeScrollbar?u("div",{class:[`${o}-drawer-content-wrapper`,this.contentClass],style:this.contentStyle,role:"none"},e):u(me,Object.assign({},this.scrollbarProps,{contentStyle:this.contentStyle,contentClass:[`${o}-drawer-content-wrapper`,this.contentClass],theme:this.mergedTheme.peers.Scrollbar,themeOverrides:this.mergedTheme.peerOverrides.Scrollbar}),e)]),this.bodyDirectives)})})),[[U,this.displayDirective==="if"||this.displayed||this.show]]):null}}),{cubicBezierEaseIn:Oe,cubicBezierEaseOut:De}=H;function We({duration:e="0.3s",leaveDuration:o="0.2s",name:t="slide-in-from-bottom"}={}){return[i(`&.${t}-transition-leave-active`,{transition:`transform ${o} ${Oe}`}),i(`&.${t}-transition-enter-active`,{transition:`transform ${e} ${De}`}),i(`&.${t}-transition-enter-to`,{transform:"translateY(0)"}),i(`&.${t}-transition-enter-from`,{transform:"translateY(100%)"}),i(`&.${t}-transition-leave-from`,{transform:"translateY(0)"}),i(`&.${t}-transition-leave-to`,{transform:"translateY(100%)"})]}const{cubicBezierEaseIn:Ae,cubicBezierEaseOut:Pe}=H;function Ne({duration:e="0.3s",leaveDuration:o="0.2s",name:t="slide-in-from-left"}={}){return[i(`&.${t}-transition-leave-active`,{transition:`transform ${o} ${Ae}`}),i(`&.${t}-transition-enter-active`,{transition:`transform ${e} ${Pe}`}),i(`&.${t}-transition-enter-to`,{transform:"translateX(0)"}),i(`&.${t}-transition-enter-from`,{transform:"translateX(-100%)"}),i(`&.${t}-transition-leave-from`,{transform:"translateX(0)"}),i(`&.${t}-transition-leave-to`,{transform:"translateX(-100%)"})]}const{cubicBezierEaseIn:Ue,cubicBezierEaseOut:Le}=H;function je({duration:e="0.3s",leaveDuration:o="0.2s",name:t="slide-in-from-right"}={}){return[i(`&.${t}-transition-leave-active`,{transition:`transform ${o} ${Ue}`}),i(`&.${t}-transition-enter-active`,{transition:`transform ${e} ${Le}`}),i(`&.${t}-transition-enter-to`,{transform:"translateX(0)"}),i(`&.${t}-transition-enter-from`,{transform:"translateX(100%)"}),i(`&.${t}-transition-leave-from`,{transform:"translateX(0)"}),i(`&.${t}-transition-leave-to`,{transform:"translateX(100%)"})]}const{cubicBezierEaseIn:Xe,cubicBezierEaseOut:Ye}=H;function _e({duration:e="0.3s",leaveDuration:o="0.2s",name:t="slide-in-from-top"}={}){return[i(`&.${t}-transition-leave-active`,{transition:`transform ${o} ${Xe}`}),i(`&.${t}-transition-enter-active`,{transition:`transform ${e} ${Ye}`}),i(`&.${t}-transition-enter-to`,{transform:"translateY(0)"}),i(`&.${t}-transition-enter-from`,{transform:"translateY(-100%)"}),i(`&.${t}-transition-leave-from`,{transform:"translateY(0)"}),i(`&.${t}-transition-leave-to`,{transform:"translateY(-100%)"})]}const Ke=i([c("drawer",`
 word-break: break-word;
 line-height: var(--n-line-height);
 position: absolute;
 pointer-events: all;
 box-shadow: var(--n-box-shadow);
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 background-color: var(--n-color);
 color: var(--n-text-color);
 box-sizing: border-box;
 `,[je(),Ne(),_e(),We(),g("unselectable",`
 user-select: none; 
 -webkit-user-select: none;
 `),g("native-scrollbar",[c("drawer-content-wrapper",`
 overflow: auto;
 height: 100%;
 `)]),$("resize-trigger",`
 position: absolute;
 background-color: #0000;
 transition: background-color .3s var(--n-bezier);
 `,[g("hover",`
 background-color: var(--n-resize-trigger-color-hover);
 `)]),c("drawer-content-wrapper",`
 box-sizing: border-box;
 `),c("drawer-content",`
 height: 100%;
 display: flex;
 flex-direction: column;
 `,[g("native-scrollbar",[c("drawer-body-content-wrapper",`
 height: 100%;
 overflow: auto;
 `)]),c("drawer-body",`
 flex: 1 0 0;
 overflow: hidden;
 `),c("drawer-body-content-wrapper",`
 box-sizing: border-box;
 padding: var(--n-body-padding);
 `),c("drawer-header",`
 font-weight: var(--n-title-font-weight);
 line-height: 1;
 font-size: var(--n-title-font-size);
 color: var(--n-title-text-color);
 padding: var(--n-header-padding);
 transition: border .3s var(--n-bezier);
 border-bottom: 1px solid var(--n-divider-color);
 border-bottom: var(--n-header-border-bottom);
 display: flex;
 justify-content: space-between;
 align-items: center;
 `,[$("main",`
 flex: 1;
 `),$("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `)]),c("drawer-footer",`
 display: flex;
 justify-content: flex-end;
 border-top: var(--n-footer-border-top);
 transition: border .3s var(--n-bezier);
 padding: var(--n-footer-padding);
 `)]),g("right-placement",`
 top: 0;
 bottom: 0;
 right: 0;
 border-top-left-radius: var(--n-border-radius);
 border-bottom-left-radius: var(--n-border-radius);
 `,[$("resize-trigger",`
 width: 3px;
 height: 100%;
 top: 0;
 left: 0;
 transform: translateX(-1.5px);
 cursor: ew-resize;
 `)]),g("left-placement",`
 top: 0;
 bottom: 0;
 left: 0;
 border-top-right-radius: var(--n-border-radius);
 border-bottom-right-radius: var(--n-border-radius);
 `,[$("resize-trigger",`
 width: 3px;
 height: 100%;
 top: 0;
 right: 0;
 transform: translateX(1.5px);
 cursor: ew-resize;
 `)]),g("top-placement",`
 top: 0;
 left: 0;
 right: 0;
 border-bottom-left-radius: var(--n-border-radius);
 border-bottom-right-radius: var(--n-border-radius);
 `,[$("resize-trigger",`
 width: 100%;
 height: 3px;
 bottom: 0;
 left: 0;
 transform: translateY(1.5px);
 cursor: ns-resize;
 `)]),g("bottom-placement",`
 left: 0;
 bottom: 0;
 right: 0;
 border-top-left-radius: var(--n-border-radius);
 border-top-right-radius: var(--n-border-radius);
 `,[$("resize-trigger",`
 width: 100%;
 height: 3px;
 top: 0;
 left: 0;
 transform: translateY(-1.5px);
 cursor: ns-resize;
 `)])]),i("body",[i(">",[c("drawer-container",`
 position: fixed;
 `)])]),c("drawer-container",`
 position: relative;
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 pointer-events: none;
 `,[i("> *",`
 pointer-events: all;
 `)]),c("drawer-mask",`
 background-color: rgba(0, 0, 0, .3);
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 `,[g("invisible",`
 background-color: rgba(0, 0, 0, 0)
 `),Ce({enterDuration:"0.2s",leaveDuration:"0.2s",enterCubicBezier:"var(--n-bezier-in)",leaveCubicBezier:"var(--n-bezier-out)"})])]),Ve=Object.assign(Object.assign({},q.props),{show:Boolean,width:[Number,String],height:[Number,String],placement:{type:String,default:"right"},maskClosable:{type:Boolean,default:!0},showMask:{type:[Boolean,String],default:!0},to:[String,Object],displayDirective:{type:String,default:"if"},nativeScrollbar:{type:Boolean,default:!0},zIndex:Number,onMaskClick:Function,scrollbarProps:Object,contentClass:String,contentStyle:[Object,String],trapFocus:{type:Boolean,default:!0},onEsc:Function,autoFocus:{type:Boolean,default:!0},closeOnEsc:{type:Boolean,default:!0},blockScroll:{type:Boolean,default:!0},maxWidth:Number,maxHeight:Number,minWidth:Number,minHeight:Number,resizable:Boolean,defaultWidth:{type:[Number,String],default:251},defaultHeight:{type:[Number,String],default:251},onUpdateWidth:[Function,Array],onUpdateHeight:[Function,Array],"onUpdate:width":[Function,Array],"onUpdate:height":[Function,Array],"onUpdate:show":[Function,Array],onUpdateShow:[Function,Array],onAfterEnter:Function,onAfterLeave:Function,drawerStyle:[String,Object],drawerClass:String,target:null,onShow:Function,onHide:Function}),Je=Y({name:"Drawer",inheritAttrs:!1,props:Ve,setup(e){const{mergedClsPrefixRef:o,namespaceRef:t,inlineThemeDisabled:p}=K(e),b=Re(),S=q("Drawer","-drawer",Ke,Me,e,o),f=B(e.defaultWidth),y=B(e.defaultHeight),v=L(j(e,"width"),f),C=L(j(e,"height"),y),M=w(()=>{const{placement:r}=e;return r==="top"||r==="bottom"?"":X(v.value)}),I=w(()=>{const{placement:r}=e;return r==="left"||r==="right"?"":X(C.value)}),O=r=>{const{onUpdateWidth:s,"onUpdate:width":l}=e;s&&x(s,r),l&&x(l,r),f.value=r},k=r=>{const{onUpdateHeight:s,"onUpdate:width":l}=e;s&&x(s,r),l&&x(l,r),y.value=r},D=w(()=>[{width:M.value,height:I.value},e.drawerStyle||""]);function R(r){const{onMaskClick:s,maskClosable:l}=e;l&&E(!1),s&&s(r)}function W(r){R(r)}const A=Fe();function P(r){var s;(s=e.onEsc)===null||s===void 0||s.call(e),e.show&&e.closeOnEsc&&He(r)&&(A.value||E(!1))}function E(r){const{onHide:s,onUpdateShow:l,"onUpdate:show":n}=e;l&&x(l,r),n&&x(n,r),s&&!r&&x(s,r)}T(V,{isMountedRef:b,mergedThemeRef:S,mergedClsPrefixRef:o,doUpdateShow:E,doUpdateHeight:k,doUpdateWidth:O});const F=w(()=>{const{common:{cubicBezierEaseInOut:r,cubicBezierEaseIn:s,cubicBezierEaseOut:l},self:{color:n,textColor:a,boxShadow:d,lineHeight:h,headerPadding:z,footerPadding:J,borderRadius:Q,bodyPadding:G,titleFontSize:Z,titleTextColor:ee,titleFontWeight:te,headerBorderBottom:re,footerBorderTop:oe,closeIconColor:ne,closeIconColorHover:ie,closeIconColorPressed:se,closeColorHover:ae,closeColorPressed:le,closeIconSize:de,closeSize:ce,closeBorderRadius:ue,resizableTriggerColorHover:he}}=S.value;return{"--n-line-height":h,"--n-color":n,"--n-border-radius":Q,"--n-text-color":a,"--n-box-shadow":d,"--n-bezier":r,"--n-bezier-out":l,"--n-bezier-in":s,"--n-header-padding":z,"--n-body-padding":G,"--n-footer-padding":J,"--n-title-text-color":ee,"--n-title-font-size":Z,"--n-title-font-weight":te,"--n-header-border-bottom":re,"--n-footer-border-top":oe,"--n-close-icon-color":ne,"--n-close-icon-color-hover":ie,"--n-close-icon-color-pressed":se,"--n-close-size":ce,"--n-close-color-hover":ae,"--n-close-color-pressed":le,"--n-close-icon-size":de,"--n-close-border-radius":ue,"--n-resize-trigger-color-hover":he}}),m=p?Te("drawer",void 0,F,e):void 0;return{mergedClsPrefix:o,namespace:t,mergedBodyStyle:D,handleOutsideClick:W,handleMaskClick:R,handleEsc:P,mergedTheme:S,cssVars:p?void 0:F,themeClass:m==null?void 0:m.themeClass,onRender:m==null?void 0:m.onRender,isMounted:b}},render(){const{mergedClsPrefix:e}=this;return u(ke,{to:this.to,show:this.show},{default:()=>{var o;return(o=this.onRender)===null||o===void 0||o.call(this),N(u("div",{class:[`${e}-drawer-container`,this.namespace,this.themeClass],style:this.cssVars,role:"none"},this.showMask?u(_,{name:"fade-in-transition",appear:this.isMounted},{default:()=>this.show?u("div",{"aria-hidden":!0,class:[`${e}-drawer-mask`,this.showMask==="transparent"&&`${e}-drawer-mask--invisible`],onClick:this.handleMaskClick}):null}):null,u(Ie,Object.assign({},this.$attrs,{class:[this.drawerClass,this.$attrs.class],style:[this.mergedBodyStyle,this.$attrs.style],blockScroll:this.blockScroll,contentStyle:this.contentStyle,contentClass:this.contentClass,placement:this.placement,scrollbarProps:this.scrollbarProps,show:this.show,displayDirective:this.displayDirective,nativeScrollbar:this.nativeScrollbar,onAfterEnter:this.onAfterEnter,onAfterLeave:this.onAfterLeave,trapFocus:this.trapFocus,autoFocus:this.autoFocus,resizable:this.resizable,maxHeight:this.maxHeight,minHeight:this.minHeight,maxWidth:this.maxWidth,minWidth:this.minWidth,showMask:this.showMask,onEsc:this.handleEsc,onClickoutside:this.handleOutsideClick}),this.$slots)),[[Ee,{zIndex:this.zIndex,enabled:this.show}]])}})}});export{Je as N};
